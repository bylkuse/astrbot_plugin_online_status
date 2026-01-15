import re
from typing import Optional

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.platform import At

from .utils import PluginConfig, CustomPresetItem, StatusView
from .services import StatusManager, ScheduleGenerator, ScheduleResource, ScheduleService
from .adapters import AstrAdapterManager, AstrHost, NapcatSerializer
from .domain import StatusSource, Duration, Fallback, QQStatus, NapcatExt, StatusFactory

try:
    apply_gemini_patch()
except Exception as e:
    logger.warning(f"[OnlineStatus] 补丁加载失败: {e}") # MonkeyPatch 应付部分中转站兼容性问题

class OnlineStatusPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config_helper = PluginConfig(config)
        self.data_dir = StarTools.get_data_dir()

        # 视图层
        self.view = StatusView(self.config_helper)

        # 宿主
        self.host = AstrHost(context, self.config_helper)

        # 文件 I/O 
        self.resource = ScheduleResource(self.data_dir)

        # 状态机
        self.manager = StatusManager(self.host, self.config_helper)

        # LLM 交互
        self.generator = ScheduleGenerator(self.host, self.config_helper, self.data_dir)

        # 预处理过滤
        self.wake_prefixes = self._load_wake_prefixes()

        # 定时任务
        self.scheduler = ScheduleService(
            resource=self.resource,
            manager=self.manager,
            generator=self.generator,
            config=self.config_helper
        )
        logger.debug("[OnlineStatus] ⚙对象组装完成 (Stage 1)")

    def _load_wake_prefixes(self) -> tuple:
        """加载并清洗唤醒前缀"""
        try:
            global_config = self.context.get_config()
            raw_prefixes = global_config.get("wake_prefix", ["/"])

            if isinstance(raw_prefixes, str):
                return (raw_prefixes,)
            elif isinstance(raw_prefixes, list):
                return tuple(raw_prefixes)
            return ("/",)
        except Exception:
            return ("/",)

    async def initialize(self):
        # 启动日程调度器的主循环
        await self.scheduler.start()
        logger.debug("[OnlineStatus] 🛎️ SS: 服务已启动，等待平台连接... (Stage 2)")

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        logger.debug("[OnlineStatus] 🩺 NA: AstrBot 加载完毕，开始绑定适配器... (Stage 3)")

        client = AstrAdapterManager.get_napcat_client(self.context)

        if client:
            from .adapters import NapcatAdapter 
            adapter = NapcatAdapter(client)

            self.manager.bind_adapter(adapter)
            logger.info(f"[OnlineStatus] ✅ NA: 绑定 Bot: {getattr(client, 'uin', 'unknown')}")
        else:
            logger.warning("[OnlineStatus] 🐧 NA: 暂未检测到 Napcat (AIOCQHTTP) 客户端连接，日程功能将仅在后台空转")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_message(self, event: AstrMessageEvent):
        """监听私聊消息触发自动唤醒"""
        # 过滤私聊指令唤醒
        if event.message_str.strip().startswith(self.wake_prefixes):
            return

        # 动态维护连接
        adapter = AstrAdapterManager.get_adapter(event)
        if adapter:
            if not self.manager.adapter or (self.manager.adapter.client != adapter.client):
                logger.debug("[OnlineStatus] 🔗 NA: 检测到活跃连接，更新 Adapter 绑定")
                self.manager.bind_adapter(adapter)

        # 触发业务逻辑
        current = self.manager._get_current_active_status()
        if current.source == StatusSource.LLM_TOOL and not current.is_expired:
            pass
        else:
            await self.manager.trigger_interaction_hook()

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """提示词注入"""
        # 1. 适配器检查
        if not self.manager.adapter:
            adapter = AstrAdapterManager.get_adapter(event) or self.host.get_napcat_adapter()
            if adapter: self.manager.bind_adapter(adapter)

        # 2. 自身状态感知
        current_status = self.manager._get_current_active_status()
        bg_status = self.manager.get_background_status()

        status_context = self.view.render_self_awareness(current_status, bg_status)

        # 3. 用户状态感知
        user_context = ""
        user_id = None
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "sender"):
            user_id = getattr(event.message_obj.sender, "user_id", None)

        if user_id and self.manager.adapter:
            try:
                target_user_status = await self.manager.adapter.get_user_status(user_id)

                if target_user_status:
                    if target_user_status.ext_status == NapcatExt.CUSTOM:
                        logger.debug(f"[OnlineStatus] 🐧 用户 {user_id} 处于自定义状态，跳过上下文注入")

                    elif target_user_status.status == QQStatus.ONLINE and target_user_status.ext_status == NapcatExt.NONE:
                        pass

                    else:
                        preset_name = self.config_helper.get_status_name_by_ids(
                            target_user_status.status, 
                            target_user_status.ext_status
                        )

                        if preset_name:
                            # 匹配预设
                            user_context = self.view.render_user_awareness(user_id, preset_name)
                            logger.info(f"[OnlineStatus] 🤔 用户 {user_id} 当前的状态是“{preset_name}")
                        else:
                            logger.debug(f"[OnlineStatus] ❓️ 用户 {user_id} 状态 ({target_user_status.status}/{target_user_status.ext_status}) 未定义(也许QQ更新了预设)")

            except Exception as e:
                logger.warning(f"[OnlineStatus] ❌ 获取用户 {user_id} 状态失败: {e}")

        # 4. 权限与工具指引
        current_p_id = await self.host.get_current_persona_id(event)
        main_p_id = await self.host.get_main_persona_id()
        is_authorized = (current_p_id == main_p_id)

        auth_prompt = self.view.render_tool_instruction(is_authorized)

        # 5. 注入
        req.system_prompt += status_context + user_context + auth_prompt

    @filter.llm_tool(name="update_qq_status")
    async def update_qq_status(
        self, 
        event: AstrMessageEvent, 
        status_name: str, 
        text_wording: Optional[str] = None,
        face_name: Optional[str] = None
    ):
        """
        更改你的 QQ 在线状态，在社交中展示你的状态、心情、日程等变化

        Args:
            status_name (string): 目标状态名。若要自定义文字，请填"custom"。
            text_wording (string): [可选] 自定义状态文字(限8字)。
            face_name (string): [可选] 自定义状态的图标名，仅 text_wording 存在时生效。
        """

        # 权限校验
        current_p_id = await self.host.get_current_persona_id(event)
        main_p_id = await self.host.get_main_persona_id()

        if current_p_id != main_p_id:
            return "静默失败：请保持人设，当前人格无法操作在线状态。"

        if not self.manager.adapter:
            client = AstrAdapterManager.get_napcat_client(self.context)
            if client:
                from .adapters import NapcatAdapter
                self.manager.bind_adapter(NapcatAdapter(client))

        if not self.manager.adapter:
            return "执行失败：插件尚未绑定到 QQ 后端，无法设置状态。"

        # 逻辑分发
        status_obj = None

        if text_wording:
            # === 自定义 ===
            target_face_id = Fallback.FACE_ID
            # 明确指定
            if face_name:
                face_preset = self.config_helper.face_presets.get(face_name)
                if face_preset:
                    target_face_id = face_preset.face_id

            # 容错
            elif status_name in self.config_helper.face_presets:
                face_preset = self.config_helper.face_presets.get(status_name)
                if face_preset:
                    target_face_id = face_preset.face_id

            # 借图标
            else:
                borrow_preset = self.config_helper.get_preset(status_name)
                if isinstance(borrow_preset, CustomPresetItem):
                    target_face_id = borrow_preset.face_id

            status_obj = StatusFactory.create_custom(
                wording=text_wording,
                face_id=target_face_id,
                source=StatusSource.LLM_TOOL,
                is_silent=False,
                duration=Duration.LLM_TOOL_SETTING
            )
            logger.info(f"[OnlineStatus] 🛠 LLM设置自定义状态: Text='{text_wording}', FaceID={target_face_id}")

        else:
            # === 纯预设 ===
            preset = self.config_helper.get_preset(status_name)
            if preset:
                status_obj = StatusFactory.from_preset(
                    preset, 
                    source=StatusSource.LLM_TOOL, 
                    duration=Duration.LLM_TOOL_SETTING
                )
                logger.info(f"[OnlineStatus] 🛠 LLM切换标准预设: {preset.name}")
            else:
                # 幻觉兜底
                status_obj = StatusFactory.create_standard(
                    status=QQStatus.ONLINE,
                    ext_status=Fallback.LLM_DEFAULT_EXT,
                    source=StatusSource.LLM_TOOL,
                    duration=Duration.LLM_TOOL_SETTING
                )
                logger.warning(f"[OnlineStatus] ❓️ LLM请求未知预设 '{status_name}'，已回退")

        await self.manager.set_llm_override(status_obj)
        return self.view.render_tool_response(status_name, text_wording)

    @filter.command_group("os")
    def os_group(self):
        """在线状态管理指令组"""
        pass
 
    @os_group.command("query")
    async def os_query(self, event: AstrMessageEvent, target: str):
        """查询用户状态: os query QQ号/@某人"""
        target = target.strip()
        query_user_id = None

        # 纯数字
        if re.match(r"^\d+$", target):
            query_user_id = int(target)

        # @ (CQ码)
        if not query_user_id:
            for component in event.message_obj.message:
                if isinstance(component, At):
                    query_user_id = component.qq
                    break

        if not query_user_id:
            yield event.plain_result("🐧 请指定有效的 QQ 号或 @某人")
            return

        adapter = AstrAdapterManager.get_adapter(event)
        if not adapter and self.manager.adapter:
            adapter = self.manager.adapter

        if not adapter:
            yield event.plain_result("❌ 无法获取适配器，使用 os adapter 尝试绑定")
            return

        status = await adapter.get_user_status(query_user_id)
        if status:
            yield event.plain_result(self.view.render_query_result(query_user_id, status))
        else:
            yield event.plain_result(f"❓️ 无法获取用户 {query_user_id} 的状态")

    @os_group.command("set")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_set(self, event: AstrMessageEvent, status_name: str):
        """设定预设状态: os set <预设名>"""
        status_name = status_name.strip()
        preset = self.config_helper.get_preset(status_name)

        if preset:
            status_obj = StatusFactory.from_preset(preset, source=StatusSource.LLM_TOOL)
            await self.manager.set_llm_override(status_obj)
            yield event.plain_result(f"✅ 切换状态为: [{status_name}]")
        else:
            available = list(self.config_helper.status_presets.keys())[:3]
            yield event.plain_result(f"❓️ 未知预设名: '{status_name}'。可用: {available}...")

    @os_group.command("custom")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_raw_custom(self, event: AstrMessageEvent, face_id: int, wording: str):
        """设定自定义状态: os custom <face_id> [自定义状态名]"""
        adapter = self.manager.adapter
        if not adapter:
            yield event.plain_result("❌ 失败: 未找到适配器")
            return

        try:
            temp_status = StatusFactory.create_custom(
                wording=wording,
                face_id=face_id,
                source=StatusSource.LLM_TOOL
            )

            action, payload = NapcatSerializer.serialize(temp_status)

            logger.warning(f"======== [RAW TEST] set_diy_online_status ========")
            logger.warning(f"Auto-Inferred Payload: {payload}")

            ret = await adapter.client.api.call_action(action, **payload)

            yield event.plain_result(f"📤 Payload: {payload}\n📥 Result: {ret}")

        except Exception as e:
            yield event.plain_result(f"❌ 发生异常: {e}")

    @filter.command_group("osd")
    def osd_group(self):
        """在线状态调试指令组"""
        pass

    @osd_group.command("adapter")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_adapter(self, event: AstrMessageEvent):
        """[调试] 触发 Napcat 适配器绑定: osd adapter"""
        client = AstrAdapterManager.get_napcat_client(self.context)
        if client:
            from .adapters import NapcatAdapter
            self.manager.bind_adapter(NapcatAdapter(client))
            yield event.plain_result(f"✅ 绑定到: {client}")
        else:
            yield event.plain_result("❌ 未找到 Napcat 客户端实例。")

    @osd_group.command("message")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_message(self, event: AstrMessageEvent):
        """[调试] 模拟私聊消息唤醒: osd message"""
        if not self.manager.adapter:
            adapter = self.host.get_napcat_adapter()
            if adapter:
                self.manager.bind_adapter(adapter)

        await self.manager.trigger_interaction_hook()

        current = self.manager._get_current_active_status()

        # 显示临时状态剩余时间
        remaining_time = None
        if current.source == StatusSource.INTERACTION and self.manager._temp_status:
            remaining_time = self.manager._temp_status.remaining_time

        result_str = self.view.render_simulation_result(current, remaining_time)
        yield event.plain_result(result_str)

    @osd_group.command("persona")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_persona(self, event: AstrMessageEvent):
        """[调试] 诊断人格ID & 权限: osd persona"""
        raw_current_id = await self.host.get_current_persona_id(event)
        raw_main_id = await self.host.get_main_persona_id()

        result_str = self.view.render_persona_debug(raw_current_id, raw_main_id)
        yield event.plain_result(result_str)

    @osd_group.command("schedule")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def os_schedule(self, event: AstrMessageEvent):
        """[调试] 重置为日程状态: osd schedule"""
        self.manager._manual_status = None
        self.manager._temp_status = None
        if self.manager._revert_task:
            self.manager._revert_task.cancel()

        from datetime import datetime
        try:
            now = datetime.now()
            await self.scheduler._apply_current_slot(now)
            current = self.manager._get_current_active_status()
            yield event.plain_result(f"✅ 已重置为日程状态: {current.wording}")
        except Exception as e:
            yield event.plain_result(f"❌ 调度异常: {e}")

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        logger.info("[OnlineStatus] 🛑 正在停止插件...再见~")
        await self.scheduler.stop()
        self.manager.shutdown()