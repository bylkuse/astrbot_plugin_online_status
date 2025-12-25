import asyncio
from datetime import datetime, date
from typing import List, Dict, Optional

from astrbot.api import logger

from ..domain import StatusSource, QQStatus, Fallback, StatusFactory, OnlineStatus

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
        logger.info("[OnlineStatus] 📅 SS: 在线状态日程调度器已启动。")

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
                await self._ensure_daily_schedule(now.date())
                await self._apply_current_slot(now)
            except Exception as e:
                logger.error(f"[OnlineStatus] ❌ SS: 日程调度循环发生异常: {e}", exc_info=True)

            # 对齐时间
            wait_seconds = 60 - datetime.now().second
            await asyncio.sleep(wait_seconds)

    async def _ensure_daily_schedule(self, today: date):
        # 缓存命中
        if self.loaded_date == today and self.current_schedule:
            return

        # 加载
        local_data = await self.resource.load_schedule(today)
        
        if local_data:
            self.current_schedule = local_data
            self.loaded_date = today
            logger.info(f"[OnlineStatus] ✅ SS: 已加载本地日程表 ({today})")
            return

        # 生成
        logger.info("[OnlineStatus] 📅 SS: 未找到今日日程，正在请求 LLM 生成...")
        new_schedule = await self.generator.generate_daily_schedule(today)

        if new_schedule:
            self.current_schedule = new_schedule
            self.loaded_date = today

            # 保存
            if await self.resource.save_schedule(today, new_schedule):
                logger.info(f"[OnlineStatus] ✅ SS: 新日程已生成并保存: {len(new_schedule)} 个时间段")
        else:
            logger.warning("[OnlineStatus] ❌ SS: 日程生成失败，将在下一周期重试。")

    def _normalize_time_str(self, t_str: str) -> str:
        """标准化为HH:MM"""
        t_str = str(t_str).replace("：", ":").strip()
        try:
            return datetime.strptime(t_str, "%H:%M").strftime("%H:%M")
        except ValueError:
            return t_str

    def _is_sleep_related(self, status_name: str, text: str) -> bool:
        """[兜底]睡觉/休息延续"""
        keywords = ["睡", "sleep", "rest", "休息", "晚安", "梦"]
        combined = (str(status_name) + str(text)).lower()
        return any(k in combined for k in keywords)

    def _get_gap_fallback_status(self, now: datetime) -> OnlineStatus:
        """[兜底]空档期逻辑"""
        hour = now.hour

        # 深夜
        if hour >= 23 or hour < 6:
            # 睡觉预设
            for name in ["睡觉中", "睡觉", "Sleep", "休息"]:
                preset = self.config.get_preset(name)
                if preset:
                    logger.info(f"[OnlineStatus] 🌙 SS: 深夜时段({hour}点)兜底 -> 应用预设 '{name}'")
                    return StatusFactory.from_preset(preset, source=StatusSource.SCHEDULE)

            # 构造睡眠状态
            logger.info(f"[OnlineStatus] 🌙 SS: 深夜时段({hour}点)兜底 -> 构造默认睡眠状态")
            return StatusFactory.create_custom(
                wording="当猪咪", 
                face_id=75,
                source=StatusSource.SCHEDULE,
                is_silent=False
            )

        # 白天
        logger.info(f"[OnlineStatus] 🌞 SS: 白天时段({hour}点)兜底")
        return StatusFactory.create_standard(
            status=QQStatus.ONLINE, 
            ext_status=Fallback.SCHEDULER_DEFAULT_EXT, 
            source=StatusSource.SCHEDULE
        )

    async def _apply_current_slot(self, now: datetime):
        if not self.current_schedule:
            fallback = self._get_gap_fallback_status(now)
            await self.manager.update_schedule(fallback)
            return

        current_time_str = now.strftime("%H:%M")
        matched_slot = None
        last_ended_slot = None

        valid_slots = []
        for slot in self.current_schedule:
            s_raw = slot.get('start', '')
            e_raw = slot.get('end', '')
            if s_raw and e_raw:
                slot['_start_norm'] = self._normalize_time_str(s_raw)
                slot['_end_norm'] = self._normalize_time_str(e_raw)
                valid_slots.append(slot)

        valid_slots.sort(key=lambda x: x['_start_norm'])

        for slot in valid_slots:
            start = slot['_start_norm']
            end = slot['_end_norm']

            is_overnight = end < start # 跨天

            is_match = False
            if is_overnight:
                if current_time_str >= start or current_time_str < end:
                    is_match = True
            else:
                if start <= current_time_str < end:
                    is_match = True

            if is_match:
                matched_slot = slot
                break

            # 寻找最近的Slot
            if not is_overnight and current_time_str >= end:
                last_ended_slot = slot

        status_obj = None

        # 日程
        if matched_slot:
            status_obj = self._create_status_from_slot(matched_slot)

        # [兜底]延续
        elif last_ended_slot:
            status_name = last_ended_slot.get('status_name', '')
            text = last_ended_slot.get('text', '')

            if self._is_sleep_related(status_name, text):
                if now.hour < 10 or now.hour >= 22:
                    logger.info(f"[OnlineStatus] 🌙 SS: 未命中日程，延续睡眠惯性 ({status_name})")
                    status_obj = self._create_status_from_slot(last_ended_slot)

        # [兜底]时段感知
        if not status_obj:
            logger.warning(f"[OnlineStatus] 🥴 SS: !!!触发时段兜底，生成的日程可能不完整!!!")
            status_obj = self._get_gap_fallback_status(now)

        await self.manager.update_schedule(status_obj)

    def _create_status_from_slot(self, slot: Dict) -> OnlineStatus:
        status_name = slot.get('status_name')
        text_wording = slot.get('text')
        face_name = slot.get('face_name') or slot.get('face')
        is_silent_raw = slot.get('is_silent')
        final_silent = bool(is_silent_raw) if is_silent_raw is not None else False

        # 自定义
        if text_wording:
            face_id = Fallback.FACE_ID
            if face_name:
                preset = self.config.face_presets.get(face_name)
                if preset: face_id = preset.face_id
            elif status_name in self.config.face_presets:
                preset = self.config.face_presets.get(status_name)
                if preset: face_id = preset.face_id

            return StatusFactory.create_custom(
                wording=text_wording,
                face_id=face_id,
                source=StatusSource.SCHEDULE,
                is_silent=final_silent
            )

        # 预设
        if status_name:
            preset = self.config.get_preset(status_name)
            if preset:
                return StatusFactory.from_preset(preset, source=StatusSource.SCHEDULE)

        # 兜底
        return StatusFactory.create_standard(
            status=QQStatus.ONLINE,
            ext_status=Fallback.SCHEDULER_DEFAULT_EXT,
            source=StatusSource.SCHEDULE
        )