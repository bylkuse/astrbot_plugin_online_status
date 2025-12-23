import asyncio
import time
from typing import Optional
from astrbot.api import logger

from ..domain import OnlineStatus, StatusSource, StatusType

class StatusManager:
    def __init__(self, host):
        self.host = host
        self._schedule_status: Optional[OnlineStatus] = None 
        self._temp_status: Optional[OnlineStatus] = None     
        self._manual_status: Optional[OnlineStatus] = None   
        self.adapter = None 
        
        # [状态机缓存] 记录上一次成功应用到 QQ 的状态
        self._last_applied_status: Optional[OnlineStatus] = None

    def bind_adapter(self, adapter):
        self.adapter = adapter

    async def update_schedule(self, status: OnlineStatus):
        """
        接收来自 Scheduler 的新日程状态
        """
        status.source = StatusSource.SCHEDULE
        
        # [核心逻辑修改] 日程阶段变更检测
        # 如果当前已经有一个旧的日程状态，且新状态与旧状态在 payload 上不一致
        # 说明时间到了，日程进入了新的阶段（例如：从“睡觉”变成了“起床”）
        # 此时，应该清除 LLM 之前设置的手动覆盖，让 Bot 回归日程表的管理。
        if self._schedule_status:
            if not status.is_payload_equal(self._schedule_status):
                if self._manual_status:
                    logger.info(f"[日程流转] 检测到日程阶段变更 ({self._schedule_status.wording} -> {status.wording})，自动清除 LLM 手动覆盖。")
                    self._manual_status = None

        self._schedule_status = status
        
        # 即使 Manual 没被清除（比如日程没变），update_schedule 也会触发一次 sync
        # sync 内部会根据优先级决定最终推送到 QQ 的状态
        await self._sync_to_platform()

    async def trigger_interaction_hook(self):
        current = self._get_current_active_status()
        
        # 如果被 LLM 锁定，不触发
        if current.source == StatusSource.LLM_TOOL and not current.is_expired:
            return 

        if not current.is_silent:
            logger.warning(f"[交互触发] 当前处于可交互状态({current.wording})，临时切换为活跃在线...")
            self._temp_status = OnlineStatus(
                type=StatusType.STANDARD,
                status=10, 
                ext_status=0,
                battery_status=0,
                is_silent=False,
                source=StatusSource.INTERACTION,
                duration=60,
                created_at=time.time()
            )
            await self._sync_to_platform()
        else:
            # 未来扩展：可以在这里注入 System Prompt 告知 LLM "我正在睡觉，但被消息唤醒了"
            # logger.debug(f"[交互触发] 当前是勿扰模式({current.wording})，忽略交互打断。")
            pass

    async def set_llm_override(self, status: OnlineStatus):
        status.source = StatusSource.LLM_TOOL
        self._manual_status = status
        logger.info(f"[LLM意识] 主动请求切换: {status.wording}")
        await self._sync_to_platform()

    def _get_current_active_status(self) -> OnlineStatus:
        # 1. Check Manual
        if self._manual_status:
            if not self._manual_status.is_expired:
                return self._manual_status
            else:
                self._manual_status = None
        
        # 2. Check Temp
        if self._temp_status:
            if self._temp_status.is_expired:
                self._temp_status = None 
            else:
                return self._temp_status
        
        # 3. Check Schedule
        if self._schedule_status:
            return self._schedule_status
            
        # 4. Default
        return OnlineStatus(
            type=StatusType.STANDARD,
            status=10, 
            ext_status=0,
            wording="Default", 
            is_silent=False,
            source=StatusSource.SCHEDULE
        )

    async def _sync_to_platform(self, force_refresh: bool = False):
        """
        将最终计算出的状态同步到平台 (状态机核心)
        """
        if not self.adapter:
            if hasattr(self.host, "get_napcat_adapter"):
                self.adapter = self.host.get_napcat_adapter()
        
        if not self.adapter:
            return

        target_status = self._get_current_active_status()
        
        # === 状态机逻辑 ===
        # 只有当目标状态与上一次成功应用的状态 不一致 时，才发起请求
        if not force_refresh and self._last_applied_status:
            if target_status.is_payload_equal(self._last_applied_status):
                # 状态未变，直接跳过 (静默期 0 网络消耗)
                return

        logger.info(f"🔄 状态变更: {self._last_applied_status.wording if self._last_applied_status else 'None'} -> {target_status.wording}")
        
        # 发起调用 (adapter 内部有重试逻辑)
        success = await self.adapter.set_custom_status(target_status)

        # 只有成功了才更新缓存
        if success:
            self._last_applied_status = target_status
            logger.info(f"✅ [状态机] 已更新缓存: {target_status.log_desc}")
        else:
            logger.warning(f"❌ [状态机] 同步失败，保持旧缓存，将在下个周期重试。")