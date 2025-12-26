import time
from typing import Optional, Dict, Any

from ..utils import StatusPresetItem, CustomPresetItem
from . import (
    QQStatus, NapcatExt, StatusSource, StatusType, 
    FaceType, Limits, Fallback,
    OnlineStatus
)


class StatusFactory:
    """实例工厂: 业务规则校验、数据清洗、默认值"""

    @staticmethod
    def create_custom(
        wording: str,
        face_id: int = Fallback.FACE_ID,
        source: StatusSource = StatusSource.LLM_TOOL,
        is_silent: bool = False,
        duration: Optional[int] = None
    ) -> OnlineStatus:

        # 文字截断
        clean_wording = StatusFactory._truncate_wording(wording, Limits.WORDING_LENGTH)

        # 推导表情类型
        computed_face_type = StatusFactory._determine_face_type(face_id)

        return OnlineStatus(
            type=StatusType.CUSTOM,
            source=source,
            wording=clean_wording,
            face_id=face_id,
            face_type=computed_face_type,
            is_silent=is_silent,
            duration=duration,
            status=QQStatus.ONLINE,
            ext_status=NapcatExt.NONE,
            created_at=time.time()
        )

    @staticmethod
    def create_standard(
        status: int,
        ext_status: int,
        source: StatusSource = StatusSource.SCHEDULE,
        is_silent: bool = False,
        duration: Optional[int] = None
    ) -> OnlineStatus:
        """创建标准状态"""
        return OnlineStatus(
            type=StatusType.STANDARD,
            source=source,
            status=status,
            ext_status=ext_status,
            is_silent=is_silent,
            duration=duration,
            face_type=FaceType.SYSTEM,
            wording="",
            created_at=time.time()
        )

    @staticmethod
    def from_preset(
        preset: Any, # StatusPresetItem or CustomPresetItem
        source: StatusSource = StatusSource.SCHEDULE,
        duration: Optional[int] = None
    ) -> OnlineStatus:
        """从预设"""

        # 自定义预设
        if isinstance(preset, CustomPresetItem):
            return StatusFactory.create_custom(
                wording=preset.wording,
                face_id=preset.face_id,
                source=source,
                is_silent=preset.is_silent,
                duration=duration
            )

        # 标准预设
        elif isinstance(preset, StatusPresetItem):
            return StatusFactory.create_standard(
                status=preset.status_id,
                ext_status=preset.ext_status_id,
                source=source,
                is_silent=preset.is_silent,
                duration=duration
            )

        # 兜底
        return StatusFactory.create_standard(
            status=QQStatus.ONLINE, 
            ext_status=Fallback.ACTIVE_DEFAULT_EXT,
            source=source
        )

    @staticmethod
    def from_napcat_payload(data: Dict[str, Any]) -> OnlineStatus:
        """Napcat返回的字典数据反序列化"""
        status_val = int(data.get("status", QQStatus.ONLINE))
        ext_val = int(data.get("ext_status", NapcatExt.NONE))

        now = time.time()

        if ext_val == NapcatExt.CUSTOM:
            return OnlineStatus(
                type=StatusType.CUSTOM,
                source=StatusSource.INTERACTION,
                face_id=0, # 未知
                wording="<Remote Custom>", # 占位
                status=status_val,
                ext_status=ext_val,
                is_silent=False,
                created_at=now
            )
        else:
            return OnlineStatus(
                type=StatusType.STANDARD,
                source=StatusSource.INTERACTION,
                status=status_val,
                ext_status=ext_val,
                is_silent=False,
                created_at=now
            )

    # --- 内部方法 ---

    @staticmethod
    def _determine_face_type(face_id: int) -> int:
        """根据 ID 范围推导类型"""
        if face_id < Limits.FACE_TYPE_THRESHOLD:
            return FaceType.SYSTEM
        return FaceType.EMOJI

    @staticmethod
    def _truncate_wording(text: str, limit: int) -> str:
        """双字节感知截断"""
        if not text: return ""
        current_len = 0
        result = ""
        for char in text:
            char_len = 2 if ord(char) > 0xFFFF else 1
            if current_len + char_len > limit:
                break
            result += char
            current_len += char_len
        return result