import asyncio
from typing import Optional
from astrbot.api import logger

from ..domain import OnlineStatus, StatusSource, Duration, QQStatus, Fallback, StatusFactory
from ..utils import StatusPresetItem

class StatusManager:
    def __init__(self, host, config):
        self.host = host
        self.config = config

        # --- 状态存储 ---
        self._schedule_status: Optional[OnlineStatus] = None  # 底层：日程
        self._manual_status: Optional[OnlineStatus] = None    # 中层：LLM 手动
        self._temp_status: Optional[OnlineStatus] = None      # 顶层：交互唤醒

        # --- 组件 ---
        self.adapter = None 
        self._last_applied_status: Optional[OnlineStatus] = None

        # --- 并发控制 ---
        self._revert_task: Optional[asyncio.Task] = None

    def bind_adapter(self, adapter):
        self.adapter = adapter

    def shutdown(self):
        """清理回收"""
        logger.info("[OnlineStatus] 🔌 SM: 正在关闭，清理定时器...")
        self._cancel_active_timer()

    def _cancel_active_timer(self):
        """[原子操作] 取消当前活跃的回落计时器"""
        if self._revert_task:
            if not self._revert_task.done():
                self._revert_task.cancel()
            self._revert_task = None

    async def update_schedule(self, status: OnlineStatus):
        """日程流转"""
        status.source = StatusSource.SCHEDULE

        # if self._schedule_status:
        #    if not status.is_payload_equal(self._schedule_status):
        #        if self._manual_status:
        #            logger.info(f"[OnlineStatus] 🧑‍💼 SM: 检测到日程变更 ({self._schedule_status.wording} -> {status.wording})，自动释放 LLM 手动锁")
        #            self._manual_status = None
        # 是否注释此处决定手动/LLM是否优先于日程，日后会做开关

        self._schedule_status = status
        await self._sync_to_platform()

    async def trigger_interaction_hook(self):
        """交互唤醒逻辑"""
        self._cancel_active_timer()

        # 判定背景状态
        bg_status = self._manual_status if (self._manual_status and not self._manual_status.is_expired) else self._schedule_status
        is_bg_silent = bg_status.is_silent if bg_status else False
        if is_bg_silent:
            return

        # 生成临时状态
        target_name = self.config.wake_up_status
        preset = self.config.get_preset(target_name)
        new_status = None

        if preset:
            new_status = StatusFactory.from_preset(
                preset, 
                source=StatusSource.INTERACTION, 
                duration=Duration.INTERACTION_HOOK
            )
            if isinstance(preset, StatusPresetItem):
                new_status.is_silent = False

        # 兜底
        if not new_status:
            logger.warning(f"[OnlineStatus] ❌ SM: 唤醒预设 '{target_name}' 未找到，使用默认兜底")
            new_status = StatusFactory.create_standard(
                status=QQStatus.ONLINE,
                ext_status=Fallback.BACKGROUND_DEFAULT_EXT,
                source=StatusSource.INTERACTION,
                duration=Duration.INTERACTION_HOOK,
                is_silent=False
            )

        self._temp_status = new_status
        await self._sync_to_platform()

        # 强引用计时器
        self._revert_task = asyncio.create_task(
            self._auto_revert_temp(Duration.INTERACTION_HOOK)
        )

    async def _auto_revert_temp(self, delay: int):
        try:
            await asyncio.sleep(delay)

            # 临界保护
            if self._revert_task is not asyncio.current_task():
                return 

            self._temp_status = None
            self._revert_task = None # 解除引用

            logger.debug("[OnlineStatus] ⏰️ SM: [自动回落] 交互状态过期，回落到背景状态。")
            await self._sync_to_platform()

        except asyncio.CancelledError:
            logger.debug("[OnlineStatus] ⏰️ SM: [自动回落] 计时器被取消/重置。")
            raise

        except Exception as e:
            logger.error(f"[OnlineStatus] ❌ SM: [自动回落] 执行异常: {e}", exc_info=True)

    async def set_llm_override(self, status: OnlineStatus):
        """LLM 手动设置状态"""
        status.source = StatusSource.LLM_TOOL
        self._manual_status = status

        self._temp_status = None 
        self._cancel_active_timer()

        logger.info(f"[OnlineStatus] 🧑‍💼 SM: LLM 主动请求切换: {status.wording}")
        await self._sync_to_platform()

    def get_background_status(self) -> OnlineStatus:
        """获取唤醒时的背景状态"""
        # 检查 LLM
        if self._manual_status and not self._manual_status.is_expired:
            return self._manual_status

        # 检查日程
        if self._schedule_status:
            return self._schedule_status

        # 兜底
        return StatusFactory.create_standard(
            status=QQStatus.ONLINE,
            ext_status=Fallback.BACKGROUND_DEFAULT_EXT,
            source=StatusSource.SCHEDULE,
            is_silent=False
        )

    def _get_current_active_status(self) -> OnlineStatus:
        """
        [核心状态机] 计算当前应该显示的状态
        优先级: Temp (最高) > Manual > Schedule (最低)
        """

        # 临时状态
        if self._temp_status:
            if not self._temp_status.is_expired:
                return self._temp_status
            else:
                self._temp_status = None

        # LLM
        if self._manual_status:
            if not self._manual_status.is_expired:
                return self._manual_status
            else:
                logger.info(f"[OnlineStatus] 🧑‍💼 SM: [状态机] LLM 手动状态 ({self._manual_status.wording}) 已过期，释放控制权。")
                self._manual_status = None

        # 日程
        if self._schedule_status:
            return self._schedule_status

        # 兜底
        return StatusFactory.create_standard(
            status=QQStatus.ONLINE, 
            ext_status=Fallback.ACTIVE_DEFAULT_EXT,
            source=StatusSource.SCHEDULE,
            is_silent=False
        )

    async def _sync_to_platform(self, force_refresh: bool = False):
        if not self.adapter and hasattr(self.host, "get_napcat_adapter"):
            self.adapter = self.host.get_napcat_adapter()

        if not self.adapter: return

        target_status = self._get_current_active_status()

        if not force_refresh and self._last_applied_status:
            if target_status.is_payload_equal(self._last_applied_status):
                return

        logger.info(f"[OnlineStatus] 🔄 SM: 状态变更: {self._last_applied_status.wording if self._last_applied_status else 'None'} -> {target_status.wording}")

        success = await self.adapter.set_custom_status(target_status)

        if success:
            self._last_applied_status = target_status
            logger.info(f"[OnlineStatus] ✅ SM: [状态机] 已更新缓存: {target_status.log_desc}")
        else:
            logger.warning(f"[OnlineStatus] ❌ SM: [状态机] 同步失败，保持旧缓存，将在下个周期重试。")