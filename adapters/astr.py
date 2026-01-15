from typing import Optional, Any

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.api.event import filter
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

from . import NapcatAdapter


_original_query = ProviderOpenAIOfficial._query

def _fix_gemini_payload(payloads: dict):
    """
    [Monkey Patch] 修复 部分 API中转 协议兼容性问题。
    Gemini API 强制要求 role='tool' 的消息必须包含 name 字段，
    但在某些情况下未传递该字段。
    """
    try:
        messages = payloads.get("messages", [])
        if not messages:
            return

        # 1. 建立索引：tool_call_id -> function_name
        id_to_name_map = {}

        for msg in messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                tool_calls = msg["tool_calls"]
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            t_id = tc.get("id")
                            func = tc.get("function", {})
                            t_name = func.get("name")
                            if t_id and t_name:
                                id_to_name_map[t_id] = t_name

        # 2. 修复数据：给 role='tool' 且缺 name 的消息补全字段
        for msg in messages:
            if msg.get("role") == "tool":
                if "name" not in msg or not msg["name"]:
                    tool_call_id = msg.get("tool_call_id")

                    # 尝试找回名字，找不到则使用默认值
                    found_name = id_to_name_map.get(tool_call_id)
                    final_name = found_name if found_name else "unknown_tool"

                    msg["name"] = final_name
                    logger.debug(f"[OnlineStatus] 🩹 Patch: 已为 tool_call_id={tool_call_id} 补全 name='{final_name}'")

    except Exception as e:
        logger.warning(f"[OnlineStatus] 🩹 Patch Warning: 修复过程异常: {e}")

async def _patched_query(self, payloads: dict, tools=None):
    # 发送前修复 payload
    _fix_gemini_payload(payloads)
    # 继续原始流程
    return await _original_query(self, payloads, tools)

def apply_gemini_patch():
    """激活 Gemini 兼容性补丁"""
    if ProviderOpenAIOfficial._query != _patched_query:
        ProviderOpenAIOfficial._query = _patched_query
        logger.info("[OnlineStatus] 🛡️ AstrBot Provider 兼容性补丁已激活 (in astr.py)")


class AstrAdapterManager:
    @staticmethod
    def get_napcat_client(context: Context) -> Optional[Any]:
        try:
            # 1. 获取平台实例
            platform = context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)

            if not platform:
                logger.debug("[OnlineStatus] 🤖 AAM: 未检测到 AIOCQHTTP 平台实例")
                return None

            # 2. 获取 Client
            client = platform.get_client()
            if client:
                return client

            logger.warning("[OnlineStatus] 🤖 AAM: 平台已加载，但 Client 为空 (Bot 可能尚未连接)")
            return None

        except Exception as e:
            logger.error(f"[OnlineStatus] ❌ AAM: 获取 Client 流程异常: {e}")
            return None

    @staticmethod
    def get_adapter(event) -> Optional[NapcatAdapter]:
        """从事件中提取适配器"""
        try:
            if event.get_platform_name() == "aiocqhttp":
                if hasattr(event, "bot"):
                    return NapcatAdapter(event.bot)
        except Exception:
            pass
        return None

class AstrHost:
    def __init__(self, context: Context, config_helper):
        self.context = context
        self.cfg_helper = config_helper

    async def get_current_persona_id(self, event) -> str:
        """获取当前会话绑定的 Persona ID"""
        found_id = None
        try:
            uid = event.unified_msg_origin
            conv_mgr = self.context.conversation_manager

            # 从会话
            curr_cid = await conv_mgr.get_curr_conversation_id(uid)
            if curr_cid:
                conversation = await conv_mgr.get_conversation(uid, curr_cid)
                if conversation and conversation.persona_id:
                    found_id = str(conversation.persona_id)

            # 全局默认
            if self._is_invalid_id(found_id):
                found_id = self._get_global_default_persona_id()

            # 已加载的第一个人格
            if self._is_invalid_id(found_id):
                all_personas = await self.context.persona_manager.get_all_personas()
                if all_personas:
                    found_id = all_personas[0].id

            return found_id if found_id else "unknown"

        except Exception as e:
            logger.error(f"[OnlineStatus] ❌ AH: 获取 Persona ID 异常: {e}")
            # 兜底
            try:
                all_personas = await self.context.persona_manager.get_all_personas()
                if all_personas:
                    return all_personas[0].id
            except Exception:
                pass
            return "unknown"

    def _is_invalid_id(self, pid: Optional[str]) -> bool:
        return not pid or pid == "None" or pid == "[%None]"

    def _get_global_default_persona_id(self) -> str:
        try:
            global_conf = self.context.get_config()

            provider_settings = global_conf.get("provider_settings", {})
            if isinstance(provider_settings, dict):
                val = provider_settings.get("default_personality")
                if val: return str(val)

            val_root = global_conf.get("default_personality")
            if val_root: return str(val_root)

            return ""
        except Exception:
            return ""

    async def get_main_persona_id(self) -> str:
        """主人格 ID"""
        # 配置优先
        configured_id = self.cfg_helper.main_persona_id
        if configured_id:
            return configured_id

        # 全局默认
        default_id = self._get_global_default_persona_id()
        if default_id:
            return default_id

        # 运行时首个
        all_personas = await self.context.persona_manager.get_all_personas()
        if all_personas:
            return all_personas[0].id

        return "unknown"

    async def get_persona_prompt(self) -> str:
        """获取主人格的人设"""
        target_id = await self.get_main_persona_id()
        persona = await self.context.persona_manager.get_persona(target_id)

        if not persona:
            return "你是一个智能助手。"

        # 标准字段
        if hasattr(persona, "system_prompt") and persona.system_prompt:
            return persona.system_prompt
        if hasattr(persona, "prompt") and persona.prompt:
            return persona.prompt

        # 字典化访问
        if hasattr(persona, "dict"):
            p_dict = persona.dict()
            return p_dict.get("system_prompt") or p_dict.get("prompt") or ""

        return "你是一个智能助手。"

    async def llm_generate_text(self, system_prompt: str, user_prompt: str, config: dict) -> str:
        """调用 AstrBot 的 LLM 接口"""
        provider_id = None

        # 优先使用配置
        if config.get("provider_id"):
            provider_id = config["provider_id"]

        # 默认 Provider
        else:
            try:
                default_provider = self.context.get_default_provider()
                if default_provider:
                    if hasattr(default_provider, "id"):
                        provider_id = default_provider.id
                    elif hasattr(default_provider, "unique_id"):
                        provider_id = default_provider.unique_id
            except Exception as e:
                logger.warning(f"[OnlineStatus] 🤖 AH: 获取默认 Provider 失败: {e}")

        if not provider_id:
            logger.error("[OnlineStatus] ❌ AH: 无法确定 LLM Provider ID。请检查 AstrBot 全局配置或插件配置。")
            return ""

        # 调用
        try:
            logger.info(f"[OnlineStatus] 🤖 AH: 正在调用 LLM ({provider_id}) 生成日程...")

            full_prompt = f"{system_prompt}\n\nUser: {user_prompt}"
            model_name = config.get("model_name") # 可为 None

            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id, 
                prompt=full_prompt,
                model_name=model_name
            )

            if llm_resp and llm_resp.completion_text:
                return llm_resp.completion_text
            else:
                logger.warning(f"[OnlineStatus] ❌ AH: LLM ({provider_id}) 返回内容为空")
                return ""

        except Exception as e:
            logger.error(f"[OnlineStatus] ❌ AH: LLM 调用过程发生异常: {e}", exc_info=True)
            return ""

    def get_napcat_adapter(self) -> Optional[NapcatAdapter]:
        client = AstrAdapterManager.get_napcat_client(self.context)
        if client:
            return NapcatAdapter(client)
        return None