import json
import os
import asyncio
import tempfile
import shutil
from datetime import date
from typing import List, Dict, Optional

from astrbot.api import logger

class ScheduleResource:
    """日程数据持久化"""
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._lock = asyncio.Lock()
        self._ensure_dir()

    def _ensure_dir(self):
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir, exist_ok=True)

    def _get_file_path(self, target_date: date) -> str:
        return os.path.join(self.base_dir, f"schedule_{target_date.isoformat()}.json")

    def _load_sync(self, file_path: str) -> Optional[List[Dict]]:
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                else:
                    logger.warning(f"[OnlineStatus] 💾 SR: 日程文件格式错误(非List): {file_path}")
                    return None
        except Exception as e:
            logger.error(f"[OnlineStatus] 💾 SR: 加载日程文件失败 [{file_path}]: {e}")
            return None

    def _save_sync(self, file_path: str, data: List[Dict]) -> bool:
        """原子写入"""
        dir_name = os.path.dirname(file_path)
        # 创建临时文件
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_name, text=True)

        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno()) # 确保写入磁盘

            # 原子替换
            shutil.move(tmp_path, file_path)
            return True
        except Exception as e:
            logger.error(f"[OnlineStatus] 💾 SR: 保存日程文件失败 [{file_path}]: {e}")
            # 清理残留临时文件
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return False

    async def load_schedule(self, target_date: date) -> Optional[List[Dict]]:
        file_path = self._get_file_path(target_date)
        async with self._lock:
            return await asyncio.to_thread(self._load_sync, file_path)

    async def save_schedule(self, target_date: date, schedule_data: List[Dict]) -> bool:
        file_path = self._get_file_path(target_date)
        async with self._lock:
            return await asyncio.to_thread(self._save_sync, file_path, schedule_data)