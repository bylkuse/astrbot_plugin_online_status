import re
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.platform import At

from .utils import PluginConfig
from .services import StatusManager, ScheduleGenerator, ScheduleResource, ScheduleService
from .adapters import AstrAdapterManager, AstrHost
from .domain import OnlineStatus, StatusSource, StatusType

class OnlineStatusPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config_helper = PluginConfig(config)
        self.data_dir = StarTools.get_data_dir()

        # 初始化各层
        self.host = AstrHost(context, self.config_helper)
        self.resource = ScheduleResource(self.data_dir)
        self.manager = StatusManager(self.host)
        self.generator = ScheduleGenerator(self.host, self.config_helper, self.data_dir)
        self.scheduler = ScheduleService(
            resource=self.resource,   # 传入资源实例
            manager=self.manager,
            generator=self.generator,
            config=self.config_helper
        )

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        
        # 1. 启动日程调度器
        await self.scheduler.start()
        
        # 2. 主动绑定 Bot (使用官方 API)
        try:
            # 使用官方文档提供的方法获取 Napcat/OneBot11 平台实例
            platform = self.context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
            
            if platform:
                # 获取该平台下所有连接的 Bot 实例
                # platform.insts 是一个字典: {qq_id: client_instance}
                # 虽然文档提到了 get_client()，但直接读取 insts 可以兼容多账号情况，默认取第一个
                insts = getattr(platform, "insts", {})
                
                if insts:
                    # 取出第一个在线的 Bot 客户端
                    bot = list(insts.values())[0]
                    
                    # 绑定 Adapter
                    from .adapters import NapcatAdapter
                    self.manager.bind_adapter(NapcatAdapter(bot))
                    
                    logger.warning(f"[OnlineStatus] 初始化成功: 已绑定 Bot ({getattr(bot, 'uin', 'unknown')})")
                else:
                    logger.warning("[OnlineStatus] AIOCQHTTP 平台已加载，但当前没有 Bot 连接。")
            else:
                logger.debug("[OnlineStatus] 未检测到 AIOCQHTTP (Napcat) 平台。")

        except Exception as e:
            # 捕获异常防止影响插件加载
            logger.warning(f"[OnlineStatus] 初始化绑定尝试失败: {e}")

        logger.info("[OnlineStatus] 插件加载完成。")

    # -----------------------------------------------------------
    # 事件钩子：LLM 请求前
    # -----------------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE) # 仅监听私聊，底层过滤更高效
    async def on_message(self, event: AstrMessageEvent):
        """
        监听私聊消息，用于触发自动唤醒 (Auto Wake-up)
        """
        # 1. 严格过滤指令
        # 凡是以 / 开头的消息被视为指令，不触发唤醒逻辑
        if event.message_str.strip().startswith("/"):
            return

        # 2. 动态绑定 Adapter (确保有发包能力)
        adapter = AstrAdapterManager.get_adapter(event)
        if adapter:
            self.manager.bind_adapter(adapter)
            
        # 3. 触发唤醒逻辑
        # logger.debug(f"[Main] 收到私聊消息: {event.message_str[:10]}... 尝试触发唤醒")
        await self.manager.trigger_interaction_hook()

    # -----------------------------------------------------------
    # 事件钩子：LLM 请求前处理
    # -----------------------------------------------------------
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        处理 Prompt 注入
        """
        current_p_id = await self.host.get_current_persona_id(event)
        main_p_id = await self.host.get_main_persona_id()

        if current_p_id == main_p_id:
            status_list_str = self.config_helper.get_status_list_prompt_str()
            inject_prompt = (
                "\n\n[社交状态]\n"
                "你可以用 `update_qq_status` 工具修改你的 QQ 在线状态显示。\n"
                "根据时间、情景你可以自主或听取意见修改你的生活安排，\n"
                "\n"
                f"可用状态预设列表:\n{status_list_str}\n"
                "\n"
                "[操作指南]\n"
                "- 优先使用列表中的 `status_name`。\n"
                "- 如果列表中没有合适的，你可以发挥想象力，在 `status_name` 填入 'custom'，并在 `text_wording` 中填入你想要显示的自定义文字（如'正在发呆'），不得超过8个任意字符。"
            )
            req.system_prompt += inject_prompt
        else:
            deny_prompt = (
                "\n\n[System Instruction]\n"
                "Please ignore the tool `update_qq_status`."
                "You are NOT authorized to verify or change online status."
            )
            req.system_prompt += deny_prompt

    # -----------------------------------------------------------
    # LLM 工具定义 (硬隔离 - 权限校验)
    # -----------------------------------------------------------
    @filter.llm_tool(name="update_qq_status")
    async def update_qq_status(self, event: AstrMessageEvent, status_name: str, text_wording: str = ""):
        """
        更改你的 QQ 在线状态，来表达你的状态、心情或日程变化。

        Args:
            status_name (string): 目标状态名称。请优先从 System Prompt 提供的 [当前可用的预设列表] 中选择（例如 "睡觉中", "忙碌"）。如果想自定义独特状态，请填 "custom"。
            text_wording (string): [可选] 仅当 status_name 为 "custom" 时填写。你想显示的自定义状态文字（如 "正在修Bug", "发呆中"）。
        """
        # 1. 权限校验
        current_p_id = await self.host.get_current_persona_id(event)
        main_p_id = await self.host.get_main_persona_id()

        logger.debug(f"[Tool Auth] Current: {repr(current_p_id)} | Main: {repr(main_p_id)}")
        
        if current_p_id != main_p_id:
            return "权限拒绝：当前人格无法操作在线状态。"

        if not self.manager.adapter:
            self.manager.bind_adapter(self.host.get_napcat_adapter())

        # 2. 尝试查找预设 (作为基准配置)
        # 哪怕 LLM 编了一个不存在的名字，get_preset 返回 None 也不影响后续逻辑
        preset = self.config_helper.get_preset(status_name)
        
        status_obj = None

        # [逻辑优化] 
        # 即使 text_wording 存在，我们也可以复用 status_name 对应预设的 face_id 和 is_silent
        # 这样 LLM 说 "update_qq_status('打游戏', '正在玩黑神话')" 时，能正确用上'打游戏'的图标
        
        if text_wording:
            # === 自定义文字模式 ===
            
            # A. 确定 Face ID
            if preset and hasattr(preset, 'face_id'):
                # 如果预设存在且有 face_id (CustomPreset)，用预设的
                target_face_id = preset.face_id
            elif preset and hasattr(preset, 'status_id'):
                # 如果是标准预设 (StatusPreset)，通常没有 face_id，只能用默认 5
                target_face_id = 21
            else:
                target_face_id = 21
            
            # B. 确定 is_silent
            if preset:
                target_is_silent = preset.is_silent
            else:
                # 没找到预设，默认认为 LLM 设定的状态是"活跃"的 (False)
                # 除非 LLM 显式说了"睡觉"等词，但这里没法判断，False 是安全的默认值
                target_is_silent = False

            status_obj = OnlineStatus(
                type=StatusType.CUSTOM,
                source=StatusSource.LLM_TOOL,
                
                face_id=target_face_id,
                # face_type 由 schema 自动推导
                wording=text_wording,
                
                is_silent=target_is_silent,
                
                # [新增] 设置一个较长的过期时间 (如 2 小时)
                # 防止 Scheduler 挂了或者长时间没日程变更时，状态永久锁死
                duration=7200, 
                created_at=0.0 # 内部会自动设为 time.time()
            )
            logger.info(f"[LLM] 请求设置自定义文本: {text_wording} (Icon:{target_face_id}, Silent:{target_is_silent})")

        else:
            # === 纯预设模式 ===
            if preset:
                # 预设模式同样给一个默认时长
                status_obj = OnlineStatus.from_preset(preset, source=StatusSource.LLM_TOOL, duration=7200)
                logger.info(f"[LLM] 请求切换标准预设: {preset.name}")
            else:
                # 预设不存在的兜底
                status_obj = OnlineStatus(
                    type=StatusType.STANDARD,
                    source=StatusSource.LLM_TOOL,
                    status=10,
                    ext_status=0,
                    wording=f"Unknown({status_name})",
                    duration=7200
                )

        await self.manager.set_llm_override(status_obj)
        return f"状态已更新为: {status_name} {text_wording}".strip()

    @filter.command_group("os")
    def os_group(self):
        """在线状态管理指令组"""
        pass

    # -----------------------------------------------------------
    # 子指令: /os adapter
    # -----------------------------------------------------------
    @os_group.command("adapter")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_adapter(self, event: AstrMessageEvent):
        """
        [调试] 手动触发 Napcat 适配器绑定
        用法: /os adapter
        """
        # 复用之前的调试逻辑
        logger.warning("=== 手动触发绑定调试 (Command: /os adapter) ===")
        
        # 调用 Host 尝试获取
        adapter = self.host.get_napcat_adapter()
        
        if adapter:
            self.manager.bind_adapter(adapter)
            yield event.plain_result(f"✅ 绑定成功！\nBot对象: {adapter.client}\n状态同步已恢复。")
        else:
            yield event.plain_result("❌ 绑定失败。请查看后台控制台的 [DEBUG] 警告日志分析原因。")

    # -----------------------------------------------------------
    # 子指令: /os query
    # -----------------------------------------------------------
    @os_group.command("query")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_query(self, event: AstrMessageEvent, target: str):
        """
        查询用户状态
        用法: /os query [QQ号/@某人]
        """
        # 逻辑复用自原 cmd_os 分支 A
        target = target.strip()
        query_user_id = None
        
        # 1. 判断是否为纯数字
        if re.match(r"^\d+$", target):
            query_user_id = int(target)
            
        # 2. 判断是否包含 @ (CQ码解析)
        if not query_user_id:
            for component in event.message_obj.message:
                if isinstance(component, At):
                    query_user_id = component.qq
                    break
        
        if not query_user_id:
            yield event.plain_result("❌ 请指定有效的 QQ 号或 @某人。")
            return

        adapter = AstrAdapterManager.get_adapter(event)
        # 如果当前事件没拿到 adapter (例如 HTTP 协议端), 尝试用 Manager 里的缓存
        if not adapter and self.manager.adapter:
            adapter = self.manager.adapter

        if not adapter:
            yield event.plain_result("❌ 无法获取适配器，请先执行 /os adapter 尝试绑定。")
            return

        status = await adapter.get_user_status(query_user_id)
        if status:
            result = (
                f"用户 {query_user_id} 当前状态:\n"
                f"----------------\n"
                f"🏷️ 主状态: {status.status}\n"
                f"🧩 扩展ID: {status.ext_status}\n"
            )
            yield event.plain_result(result)
        else:
            yield event.plain_result(f"⚠️ 无法获取用户 {query_user_id} 的状态。")

    # -----------------------------------------------------------
    # 子指令: /os set
    # -----------------------------------------------------------
    @os_group.command("set")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_set(self, event: AstrMessageEvent, status_name: str):
        """
        强制切换我的状态
        用法: /os set <预设名>
        """
        status_name = status_name.strip()
        status_obj = None

        # 1. 优先匹配自定义预设
        custom_preset = self.config_helper.custom_presets.get(status_name)
        if custom_preset:
            status_obj = OnlineStatus(
                type=StatusType.CUSTOM,
                source=StatusSource.LLM_TOOL, # 人工指令等同于 LLM
                face_id=custom_preset.face_id,
                face_type=custom_preset.face_type,
                wording=custom_preset.wording,
                is_silent=custom_preset.is_silent
            )
        else:
            # 2. 匹配标准预设
            std_preset = self.config_helper.status_presets.get(status_name)
            if std_preset:
                status_obj = OnlineStatus.from_preset(std_preset, source=StatusSource.LLM_TOOL)

        if status_obj:
            await self.manager.set_llm_override(status_obj)
            yield event.plain_result(f"✅ 已强制切换状态为: [{status_name}]")
        else:
            available = ", ".join(list(self.config_helper.status_presets.keys())[:5] + list(self.config_helper.custom_presets.keys())[:5])
            yield event.plain_result(f"❌ 未知预设名: '{status_name}'。\n可用: {available}...")
    
    @os_group.command("message")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_message(self, event: AstrMessageEvent):
        """
        [调试] 模拟收到私聊消息，触发自动唤醒逻辑
        (不消耗 Token，直接执行 manager.trigger_interaction_hook)
        用法: /os message
        """
        # 1. 尝试确保 Adapter 已绑定
        if not self.manager.adapter:
            adapter = self.host.get_napcat_adapter()
            if adapter:
                self.manager.bind_adapter(adapter)
        
        # 2. 手动触发唤醒钩子
        logger.info("[Command] 手动触发交互唤醒钩子 (/os message)")
        await self.manager.trigger_interaction_hook()

        # 3. 获取触发后的结果状态进行反馈
        # 给一点点时间让异步任务完成状态切换(虽然后台是await的，但为了保险)
        current = self.manager._get_current_active_status()
        
        status_desc = (
            f"✅ 已模拟消息交互。\n"
            f"----------------\n"
            f"当前状态: {current.wording}\n"
            f"类型: {current.type.name}\n"
            f"来源: {current.source.name}\n"
            f"静默: {current.is_silent}"
        )
        
        # 如果是临时状态，显示剩余时间
        if current.source == StatusSource.INTERACTION and self.manager._temp_status:
            remain = self.manager._temp_status.remaining_time
            status_desc += f"\n剩余时间: {remain}s"
            
        yield event.plain_result(status_desc)
    
    @os_group.command("status")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_raw_status(self, event: AstrMessageEvent, status: int, ext_status: int, battery_status: int = 0):
        """
        [底层测试] 直接调用 set_online_status
        用法: /os status <status> <ext_status> <battery_status>
        示例: /os status 10 1032 0
        """
        # 1. 获取连接 (不做任何状态机处理)
        adapter = self.manager.adapter
        if not adapter:
            adapter = self.host.get_napcat_adapter()
        
        if not adapter:
            yield event.plain_result("❌ 失败: 未找到 Napcat 适配器连接")
            return

        # 2. 构造原始 Payload
        payload = {
            "status": status,
            "ext_status": ext_status,
            "battery_status": battery_status
        }

        # 3. 发送并回显原始结果
        try:
            logger.warning(f"======== [RAW TEST] set_online_status ========")
            logger.warning(f"Payload: {payload}")
            
            # 直接调用底层 API
            ret = await adapter.client.api.call_action("set_online_status", **payload)
            
            logger.warning(f"Result: {ret}")
            yield event.plain_result(f"📤 Payload: {payload}\n📥 Result: {ret}")
            
        except Exception as e:
            logger.error(f"RAW TEST EXCEPTION: {e}")
            yield event.plain_result(f"❌ 发生异常: {e}")

    @os_group.command("custom")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_raw_custom(self, event: AstrMessageEvent, face_id: int, face_type: int, wording: str):
        """
        [底层测试] 直接调用 set_diy_online_status
        用法: /os custom <face_id> <face_type> <wording>
        示例: /os custom 10 1 测试文本
        """
        # 1. 获取连接
        adapter = self.manager.adapter
        if not adapter:
            adapter = self.host.get_napcat_adapter()
        
        if not adapter:
            yield event.plain_result("❌ 失败: 未找到 Napcat 适配器连接")
            return

        # 2. 构造原始 Payload
        payload = {
            "face_id": face_id,
            "face_type": face_type,
            "wording": wording
        }

        # 3. 发送并回显原始结果
        try:
            logger.warning(f"======== [RAW TEST] set_diy_online_status ========")
            logger.warning(f"Payload: {payload}")
            
            ret = await adapter.client.api.call_action("set_diy_online_status", **payload)
            
            logger.warning(f"Result: {ret}")
            yield event.plain_result(f"📤 Payload: {payload}\n📥 Result: {ret}")
            
        except Exception as e:
            logger.error(f"RAW TEST EXCEPTION: {e}")
            yield event.plain_result(f"❌ 发生异常: {e}")

    @os_group.command("persona")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_persona(self, event: AstrMessageEvent):
        """
        [调试] 诊断人格权限 ID
        用法: /os persona
        """
        raw_current_id = await self.host.get_current_persona_id(event)
        raw_main_id = await self.host.get_main_persona_id()
        
        is_match = (raw_current_id == raw_main_id)
        
        result = (
            f"🕵️‍♂️ 人格权限诊断 (Persona Debug)\n"
            f"=============================\n"
            f"🔹 Event.persona_id (当前): {repr(raw_current_id)}\n"
            f"🔸 Host.main_id     (预设): {repr(raw_main_id)}\n"
            f"=============================\n"
            f"⚖️ 匹配结果: {'✅ 通过' if is_match else '❌ 拒绝'}\n"
        )
        yield event.plain_result(result)

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        # 停止日程调度器，清理后台任务
        await self.scheduler.stop()
        logger.info("[OnlineStatus] 插件已停止。")