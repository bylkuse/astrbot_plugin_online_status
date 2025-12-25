import json
import asyncio
import tempfile
import shutil
from datetime import date
from typing import List, Dict, Optional, Union
from pathlib import Path

from astrbot.api import logger

class ScheduleResource:
    """日程数据持久化"""
    def __init__(self, base_dir: Union[str, Path]):
        self.base_dir = Path(base_dir)
        self._lock = asyncio.Lock()
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"[OnlineStatus] 💾 SR: 无法创建数据目录 [{self.base_dir}]: {e}")

    def _get_file_path(self, target_date: date) -> Path:
        filename = f"schedule_{target_date.isoformat()}.json"
        return self.base_dir / filename

    def _load_sync(self, file_path: Path) -> Optional[List[Dict]]:
        if not file_path.exists():
            return None
        try:
            content = file_path.read_text(encoding='utf-8')
            data = json.loads(content)

            if isinstance(data, list):
                return data
            else:
                logger.warning(f"[OnlineStatus] 💾 SR: 日程文件格式错误(非List): {file_path}")
                return None
        except Exception as e:
            logger.error(f"[OnlineStatus] 💾 SR: 加载日程文件失败 [{file_path}]: {e}")
            return None

    def _save_sync(self, file_path: Path, data: List[Dict]) -> bool:
        """原子写入"""
        dir_name = file_path.parent

        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=str(dir_name),
            suffix=".json.tmp",
            prefix="schedule_"
        )
        tmp_path = Path(tmp_path_str)

        try:
            json_data = json.dumps(data, ensure_ascii=False, indent=2)

            # 写入临时文件
            with open(tmp_fd, 'w', encoding='utf-8') as f:
                f.write(json_data)
                f.flush()
                # 确保数据写入
                import os 
                os.fsync(f.fileno())

            # 原子替换
            shutil.move(tmp_path, file_path)
            return True

        except Exception as e:
            logger.error(f"[OnlineStatus] 💾 SR: 保存日程文件失败 [{file_path}]: {e}")
            # 清理
            if tmp_path.exists():
                tmp_path.unlink()
            return False

    async def load_schedule(self, target_date: date) -> Optional[List[Dict]]:
        file_path = self._get_file_path(target_date)
        async with self._lock:
            return await asyncio.to_thread(self._load_sync, file_path)

    async def save_schedule(self, target_date: date, schedule_data: List[Dict]) -> bool:
        file_path = self._get_file_path(target_date)
        async with self._lock:
            return await asyncio.to_thread(self._save_sync, file_path, schedule_data)