import asyncio
from datetime import datetime, date
from typing import List, Dict, Optional

from astrbot.api import logger

from ..utils import PluginConfig
from ..domain import OnlineStatus, StatusSource, StatusType
from . import ScheduleGenerator, StatusManager, ScheduleResource

class ScheduleService:
    def __init__(self, resource, manager, generator, config):
        self.resource = resource
        self.manager = manager
        self.generator = generator
        self.config = config
        
        self.current_schedule: List[Dict] = []
        self.loaded_date: Optional[date] = None
        
        self._running = False
        self._task = None

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("在线状态日程调度器已启动。")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        while self._running:
            try:
                now = datetime.now()
                # 1. 确保有今日日程
                await self._ensure_daily_schedule(now.date())
                # 2. 应用当前时间段
                await self._apply_current_slot(now)
            except Exception as e:
                logger.error(f"日程调度循环发生异常: {e}", exc_info=True)
            
            # 对齐时间
            wait_seconds = 60 - datetime.now().second
            await asyncio.sleep(wait_seconds)

    async def _ensure_daily_schedule(self, today: date):
        # 内存缓存命中
        if self.loaded_date == today and self.current_schedule:
            return

        # 1. 委托 Resource 层加载
        local_data = self.resource.load_schedule(today)
        
        if local_data:
            self.current_schedule = local_data
            self.loaded_date = today
            logger.info(f"已加载本地日程表 ({today})")
            return

        # 2. 本地没有，调用 Generator 生成 (LLM)
        logger.info("未找到今日日程，正在请求 LLM 生成...")
        new_schedule = await self.generator.generate_daily_schedule(today)
        
        if new_schedule:
            self.current_schedule = new_schedule
            self.loaded_date = today
            
            # 3. 委托 Resource 层保存
            if self.resource.save_schedule(today, new_schedule):
                logger.info(f"新日程已生成并保存: {len(new_schedule)} 个时间段")
        else:
            logger.warning("日程生成失败，将在下一周期重试。")

    async def _apply_current_slot(self, now: datetime):    
        if not self.current_schedule:
            return

        current_time_str = now.strftime("%H:%M")
        matched_slot = None
        
        for slot in self.current_schedule:
            if slot['start'] <= current_time_str < slot['end']:
                matched_slot = slot
                break
        
        if matched_slot:
            status_name = matched_slot.get('status_name')
            text_wording = matched_slot.get('text')
            face_name = matched_slot.get('face_name')
            is_silent_raw = matched_slot.get('is_silent') 
            
            status_obj = None

            # === 路径 A: LLM 现编自定义状态 ===
            if text_wording and face_name:
                face_preset = self.config.face_presets.get(face_name)
                # 默认值处理
                face_id = face_preset.face_id if face_preset else 21
                
                if is_silent_raw is None:
                    final_silent = False 
                    logger.warning(f"LLM 生成了自定义状态 '{text_wording}' 但未提供 is_silent，默认设为 {final_silent}")
                else:
                    final_silent = bool(is_silent_raw)

                status_obj = OnlineStatus(
                    type=StatusType.CUSTOM,
                    source=StatusSource.SCHEDULE,
                    face_id=face_id,
                    wording=text_wording,
                    is_silent=final_silent
                )
            
            # === 路径 B: 调用预设 ===
            elif status_name:
                custom_preset = self.config.custom_presets.get(status_name)
                if custom_preset:
                    status_obj = OnlineStatus(
                        type=StatusType.CUSTOM,
                        source=StatusSource.SCHEDULE,
                        face_id=custom_preset.face_id,
                        wording=custom_preset.wording,
                        is_silent=custom_preset.is_silent
                    )
                else:
                    std_preset = self.config.status_presets.get(status_name)
                    if std_preset:
                        status_obj = OnlineStatus.from_preset(std_preset, source=StatusSource.SCHEDULE)

            if not status_obj:
                status_obj = OnlineStatus(
                    type=StatusType.STANDARD,
                    status=10, 
                    ext_status=0, 
                    is_silent=False,
                    wording="Default"
                )
            
            await self.manager.update_schedule(status_obj)