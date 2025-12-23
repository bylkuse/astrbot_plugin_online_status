from dataclasses import dataclass, field
from enum import IntEnum, Enum
from typing import Optional, Dict, Any
import time
import re

class StatusSource(IntEnum):
    SCHEDULE = 10       
    INTERACTION = 50    
    LLM_TOOL = 100      

class StatusType(Enum):
    STANDARD = "standard"  
    CUSTOM = "custom"      

@dataclass
class OnlineStatus:
    # --- 元数据 ---
    source: StatusSource = StatusSource.SCHEDULE
    type: StatusType = StatusType.STANDARD
    duration: Optional[int] = None
    created_at: float = 0.0
    
    # --- 标准接口参数 ---
    status: int = 10
    ext_status: int = 0
    battery_status: int = 0
    
    # --- 自定义接口参数 ---
    face_id: int = 0
    face_type: int = 1 # 默认给1，但在 post_init 中会根据 ID 自动修正
    wording: str = ""

    # --- 核心逻辑属性 ---
    is_silent: bool = False 

    def __post_init__(self):
        # 1. 补全创建时间
        if self.created_at == 0.0:
            self.created_at = time.time()

        # 2. 字数截断
        if self.type == StatusType.CUSTOM and self.wording:
            self.wording = self._truncate_wording(self.wording, limit=8)
            
        # 3. [新增] 自动推导 face_type
        # 规律: face_id < 5027 通常为 type 1 (黄色小圆脸/系统图标)
        #       face_id > 5027 (如 9xxx) 通常为 type 2 (大表情/特殊图标)
        if self.type == StatusType.CUSTOM:
            if self.face_id < 5027:
                self.face_type = 1
            else:
                self.face_type = 2

    def _truncate_wording(self, text: str, limit: int) -> str:
        current_len = 0
        result = ""
        for char in text:
            char_len = 2 if ord(char) > 0xFFFF else 1
            if current_len + char_len > limit:
                break
            result += char
            current_len += char_len
        return result

    @property
    def is_expired(self) -> bool:
        if self.duration is None:
            return False
        age = time.time() - self.created_at
        return age > self.duration

    @property
    def remaining_time(self) -> int:
        if self.duration is None:
            return 999999
        return int(self.duration - (time.time() - self.created_at))

    def get_api_endpoint(self) -> str:
        if self.type == StatusType.CUSTOM:
            return 'set_diy_online_status'
        return 'set_online_status'

    def get_payload(self) -> Dict[str, Any]:
        if self.type == StatusType.CUSTOM:
            return {
                "face_id": self.face_id,
                "face_type": self.face_type,
                "wording": self.wording
            }
        else:
            return {
                "status": self.status,
                "ext_status": self.ext_status,
                "battery_status": self.battery_status
            }
    
    # ================= [状态机核心] =================
    def is_payload_equal(self, other: 'OnlineStatus') -> bool:
        """
        判断两个状态在协议层面上是否实质相同。
        忽略 created_at, source, duration, is_silent 等本地元数据。
        """
        if other is None:
            return False
            
        # 1. 类型必须相同
        if self.type != other.type:
            return False
            
        # 2. 根据类型比较关键字段
        if self.type == StatusType.STANDARD:
            # 标准状态：看 status 和 ext_status
            return (self.status == other.status) and \
                   (self.ext_status == other.ext_status)
        
        elif self.type == StatusType.CUSTOM:
            # 自定义状态：只看 face_id 和 wording
            # (face_type 已由 ID 决定，且 face_id 足够唯一，故忽略 type 对比)
            return (self.face_id == other.face_id) and \
                   (self.wording == other.wording)
                   
        return False

    @property
    def log_desc(self) -> str:
        base = f"[{self.source.name}]"
        if self.type == StatusType.CUSTOM:
            return f"{base} DIY: {self.wording} (ID:{self.face_id}/T:{self.face_type})"
        return f"{base} STD: Main:{self.status} Ext:{self.ext_status} Silent:{self.is_silent}"

    @classmethod
    def from_preset(cls, preset_item, source=StatusSource.SCHEDULE, duration=None):
        return cls(
            type=StatusType.STANDARD,
            source=source,
            status=preset_item.status_id,
            ext_status=preset_item.ext_status_id,
            battery_status=0,
            is_silent=preset_item.is_silent,
            duration=duration,
            wording=preset_item.name 
        )

    @classmethod
    def from_napcat_data(cls, data: dict):
        status = int(data.get("status", 10))
        ext = int(data.get("ext_status", 0))
        
        # [新增] 识别 2000 为自定义状态
        if ext == 2000:
            return cls(
                type=StatusType.CUSTOM, # 标记为自定义
                source=StatusSource.INTERACTION, # 来源标为查询
                face_id=0, # 未知
                wording="<Remote Custom Status>", # 占位符，表明这是远程查回来的，没有具体文字
                status=status,
                ext_status=ext,
                is_silent=False 
            )
        else:
            return cls(
                type=StatusType.STANDARD,
                source=StatusSource.INTERACTION,
                status=status,
                ext_status=ext,
                is_silent=False, 
                wording=f"Standard(Main:{status}, Ext:{ext})"
            )