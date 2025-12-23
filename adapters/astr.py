from astrbot.api import logger
from astrbot.api.star import Context
from . import NapcatAdapter

class AstrAdapterManager:
    """负责协议端适配 (Protocol Adapter)"""
    @staticmethod
    def get_adapter(event):
        platform_name = event.get_platform_name()
        if platform_name == "aiocqhttp":
            return NapcatAdapter(event.bot)
        return None

class AstrHost:
    def __init__(self, context: Context, config_helper):
        self.context = context
        self.cfg_helper = config_helper # 注入 PluginConfig 实例

    async def get_current_persona_id(self, event) -> str:
        """
        根据官方标准流程，通过 ConversationManager 获取当前会话绑定的 Persona ID
        """
        found_id = None

        try:
            uid = event.unified_msg_origin
            conv_mgr = self.context.conversation_manager
            curr_cid = await conv_mgr.get_curr_conversation_id(uid)

            if curr_cid:
                conversation = await conv_mgr.get_conversation(uid, curr_cid)
                if conversation and conversation.persona_id:
                    found_id = str(conversation.persona_id)
            
            if not found_id or found_id == "None" or found_id == "[%None]":
                logger.debug(f"[AstrHost] 会话未绑定有效人格 (获取到: {found_id})，尝试获取全局默认...")
                found_id = self._get_global_default_persona_id()
            
            if not found_id or found_id == "None" or found_id == "[%None]":
                logger.debug("[AstrHost] 全局默认人格无效，强制使用已加载的第一个人格作为兜底...")
                all_personas = await self.context.persona_manager.get_all_personas()
                if all_personas:
                    found_id = all_personas[0].id
            
            # 4. 最终兜底
            return found_id if found_id else "unknown"
            
        except Exception as e:
            logger.error(f"[AstrHost] 获取当前 Persona ID 流程异常: {e}")
            # 发生异常时，也尝试返回第一个人格
            try:
                all_personas = await self.context.persona_manager.get_all_personas()
                if all_personas:
                    return all_personas[0].id
            except:
                pass
            return "unknown"

    def _get_global_default_persona_id(self) -> str:
        """
        辅助方法：获取 AstrBot 全局配置的默认人格 ID
        """
        try:
            global_conf = self.context.get_config()
            
            # 1. 尝试从 provider_settings 中获取 (标准路径)
            provider_settings = global_conf.get("provider_settings", {})

            if isinstance(provider_settings, dict):
                val = provider_settings.get("default_personality")
                if val:
                    return str(val)
            
            val_root = global_conf.get("default_personality")
            if val_root:
                return str(val_root)
                
            return ""
        except Exception as e:
            logger.warning(f"[AstrHost] 读取全局默认人格配置失败: {e}")
            return ""

    async def get_main_persona_id(self) -> str:
        """
        获取经过计算的最终主人格 ID
        优先级: 插件配置 > AstrBot全局默认配置 > 抛出异常/返回None
        """
        configured_id = self.cfg_helper.main_persona_id
        if configured_id:
            return configured_id

        default_id = self._get_global_default_persona_id()
        
        if default_id:
            return default_id
            
        # 3. 加上 await
        all_personas = await self.context.persona_manager.get_all_personas()
        if all_personas:
            return all_personas[0].id
            
        return "unknown"

    async def get_persona_prompt(self) -> str:
        """获取主人格的系统提示词（用于生成日程）"""
        target_id = await self.get_main_persona_id()
        
        persona = await self.context.persona_manager.get_persona(target_id)
        
        if persona:
            # [修改点] 尝试获取 system_prompt，如果失败则打印属性列表帮助调试
            # AstrBot 不同版本字段可能不同 (prompt / system_prompt / instruction)
            if hasattr(persona, "system_prompt"):
                return persona.system_prompt
            elif hasattr(persona, "prompt"):
                return persona.prompt
            else:
                # 调试代码：如果两个都没有，打印所有属性到日志，方便排查
                logger.warning(f"[OnlineStatus] Persona 对象属性列表: {dir(persona)}")
                # 尝试通过 dict 获取 (如果是 Pydantic v1/v2 兼容性问题)
                if hasattr(persona, "dict"):
                    return persona.dict().get("system_prompt", "")
                return "你是一个智能助手。"
        else:
            return "你是一个智能助手。"

    async def llm_generate_text(self, system_prompt: str, user_prompt: str, config: dict) -> str:
        """
        调用 AstrBot 的 LLM 接口生成文本
        """
        provider_id = config.get("provider_id")
        model_name = config.get("model_name")
        provider = None

        # 1. 尝试获取配置指定的 Provider
        if provider_id:
            try:
                provider = self.context.get_provider_by_id(provider_id)
            except Exception as e:
                logger.warning(f"获取指定 Provider({provider_id}) 失败: {e}")

        # 2. 如果没指定或获取失败，尝试获取系统默认 Provider
        if not provider and hasattr(self.context, "get_default_provider"):
            try:
                provider = self.context.get_default_provider()
            except Exception as e:
                logger.warning(f"获取默认 Provider 失败: {e}")

        if not provider:
            logger.error("❌ 日程生成失败: 未找到可用的 LLM Provider。")
            return ""

        # [修复逻辑] 更加稳健地获取 provider_id
        # 1. 如果之前是通过 ID 获取的，直接使用
        if not provider_id:
            # 2. 尝试从对象属性获取
            if hasattr(provider, "id"):
                provider_id = provider.id
            elif hasattr(provider, "unique_id"):
                provider_id = provider.unique_id
            # 3. 反向查找: 遍历管理器中的 provider 列表匹配实例
            elif hasattr(self.context, "provider_manager"):
                for pid, p_instance in self.context.provider_manager.providers.items():
                    if p_instance is provider:
                        provider_id = pid
                        break
            
            # 4. 尝试从 config 获取
            if not provider_id and hasattr(provider, "config") and isinstance(provider.config, dict):
                provider_id = provider.config.get("id")

        if not provider_id:
            # 实在找不到，打印 dir 帮助调试，并尝试盲猜 (对于 OpenAI 通常是 openai)
            logger.warning(f"⚠️ 无法确定 Provider ID，对象属性: {dir(provider)}。尝试使用 'openai' 作为默认值。")
            provider_id = "openai" # 最后的兜底，防止崩溃

        # 3. 构造请求参数
        try:
            # [修改点] 使用解析出的 provider_id 变量，而不是访问 provider.id
            logger.info(f"正在调用 LLM ({provider_id}) 生成日程...")
            
            full_prompt = f"{system_prompt}\n\nUser: {user_prompt}"
            
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id, # 使用字符串 ID
                prompt=full_prompt,
                model_name=model_name if model_name else None
            )
            
            if llm_resp and llm_resp.completion_text:
                return llm_resp.completion_text
            else:
                logger.warning("LLM 返回内容为空")
                return ""
                
        except Exception as e:
            logger.error(f"LLM 调用过程发生异常: {e}", exc_info=True)
            return ""

    def get_napcat_adapter(self):
        """尝试动态获取 Napcat 适配器 (最终适配版)"""
        logger.warning("🔍 [DEBUG] 开始执行 get_napcat_adapter...")
        
        try:
            # 1. 获取平台实例
            try:
                from astrbot.api.event import filter
                p_type = filter.PlatformAdapterType.AIOCQHTTP
            except Exception:
                p_type = "aiocqhttp"

            platform = self.context.get_platform(p_type)
            if not platform:
                logger.warning(f"❌ [DEBUG] 未找到平台 {p_type}")
                return None
            
            logger.warning(f"✅ [DEBUG] 获取到平台实例: {type(platform).__name__}")

            # 2. 获取 Bot 客户端
            # 根据刚才的日志，属性里有 'get_client' 和 'bot'，没有 'insts'
            client = None
            
            # 优先尝试官方推荐的 get_client() 方法
            if hasattr(platform, "get_client"):
                try:
                    client = platform.get_client()
                    if client:
                        logger.warning(f"✅ [DEBUG] 通过 platform.get_client() 成功获取 Bot")
                except Exception as e:
                    logger.warning(f"⚠️ [DEBUG] 调用 get_client() 出错: {e}")
            
            # 如果没获取到，尝试直接读取 .bot 属性
            if not client and hasattr(platform, "bot"):
                client = getattr(platform, "bot", None)
                if client:
                    logger.warning(f"✅ [DEBUG] 通过 platform.bot 属性成功获取 Bot")

            if not client:
                logger.warning("❌ [DEBUG] 无法获取 Bot 客户端实例 (get_client() 返回空且 .bot 属性为空)")
                return None

            # 3. 验证 Client 有效性 (可选)
            # 只是简单检查一下是否有 api 属性，防止获取到未初始化的对象
            if not hasattr(client, "api"):
                logger.warning(f"⚠️ [DEBUG] 获取到的 Client 对象似乎不完整 (缺少 .api 属性): {dir(client)}")
            
            # 4. 包装并返回
            from .napcat import NapcatAdapter
            return NapcatAdapter(client)
            
        except Exception as e:
            logger.error(f"❌ [DEBUG] get_napcat_adapter 异常: {e}", exc_info=True)