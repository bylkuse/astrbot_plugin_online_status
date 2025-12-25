from dataclasses import dataclass
from typing import Optional, Any
import time
from pydantic import BaseModel, model_validator, field_validator

from . import QQStatus, NapcatExt, StatusSource, StatusType, Duration

@dataclass
class OnlineStatus:
    # --- 核心数据 ---
    source: StatusSource = StatusSource.SCHEDULE
    type: StatusType = StatusType.STANDARD


    # --- 标准 ---
    status: int = QQStatus.ONLINE
    ext_status: int = NapcatExt.NONE
    battery_status: int = 0

    # --- 自定义 ---
    face_id: int = 0
    face_type: Optional[int] = None 
    wording: str = ""

    # --- 本地控制 ---
    is_silent: bool = False
    duration: Optional[int] = None
    created_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        if self.duration is None:
            return False
        return (time.time() - self.created_at) > self.duration

    @property
    def remaining_time(self) -> int:
        if self.duration is None:
            return Duration.INFINITE
        return int(max(0, self.duration - (time.time() - self.created_at)))

    def is_payload_equal(self, other: 'OnlineStatus') -> bool:
        """状态比对，忽略本地元数据"""
        if other is None: return False
        if self.type != other.type: return False

        if self.type == StatusType.STANDARD:
            return (self.status == other.status) and (self.ext_status == other.ext_status)
        elif self.type == StatusType.CUSTOM:
            return (self.face_id == other.face_id) and (self.wording == other.wording)

        return False

    @property
    def log_desc(self) -> str:
        base = f"[{self.source.name}]"
        if self.type == StatusType.CUSTOM:
            return f"{base} DIY: {self.wording} (ID:{self.face_id})"
        return f"{base} STD: Main:{self.status} Ext:{self.ext_status}"

class ScheduleItem(BaseModel):
    """标准结构"""
    start: str
    end: str
    status_name: Optional[str] = None
    text: Optional[str] = None
    face_name: Optional[str] = None
    is_silent: bool = False

    # 别名归一化 & 结构清洗
    @model_validator(mode='before')
    @classmethod
    def normalize_data(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        alias_map = {
            "text": ["description", "desc", "activity", "wording", "content", "status_text", "detail", "summary"],
            "face_name": ["face", "icon", "emoji", "sticker"],
            "is_silent": ["silent", "mute", "quiet"]
        }

        for standard_field, aliases in alias_map.items():
            if standard_field not in data:
                for alias in aliases:
                    if data.get(alias) is not None:
                        data[standard_field] = data[alias]
                        break

        # 时间拆分
        if "start" not in data and "time" in data:
            time_str = str(data["time"]).replace("：", ":")
            if "-" in time_str:
                parts = time_str.split("-")
                if len(parts) >= 2:
                    data["start"] = parts[0].strip()
                    data["end"] = parts[1].strip()

        # 互斥逻辑
        if data.get("status_name"):
            data.pop("text", None)
            data.pop("face_name", None)

        return data

    # 时间格式
    @field_validator('start', 'end', mode='before')
    @classmethod
    def clean_time_string(cls, v):
        if v is None: return ""
        return str(v).replace("：", ":").strip()