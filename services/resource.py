import json
import os
from datetime import date
from typing import List, Dict, Optional

from astrbot.api import logger

class ScheduleResource:
    """
    负责日程数据的持久化存储（Repository Pattern）
    """
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._ensure_dir()

    def _ensure_dir(self):
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir, exist_ok=True)

    def _get_file_path(self, target_date: date) -> str:
        return os.path.join(self.base_dir, f"schedule_{target_date.isoformat()}.json")

    def load_schedule(self, target_date: date) -> Optional[List[Dict]]:
        """
        加载指定日期的日程
        :return: 如果文件存在且解析成功，返回 List；否则返回 None
        """
        file_path = self._get_file_path(target_date)
        
        if not os.path.exists(file_path):
            return None
            
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                else:
                    logger.warning(f"日程文件格式错误(非List): {file_path}")
                    return None
        except Exception as e:
            logger.error(f"加载日程文件失败 [{file_path}]: {e}")
            return None

    def save_schedule(self, target_date: date, schedule_data: List[Dict]) -> bool:
        """
        保存日程到磁盘
        """
        file_path = self._get_file_path(target_date)
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(schedule_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存日程文件失败 [{file_path}]: {e}")
            return False