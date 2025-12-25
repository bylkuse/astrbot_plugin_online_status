from enum import IntEnum, Enum

# 协议常量

class QQStatus(IntEnum):
    ONLINE = 10
    OFFLINE = 20
    AWAY = 30
    INVISIBLE=40
    BUSY = 50
    DONTDISTURB = 70

class NapcatExt(IntEnum):
    NONE = 0
    CUSTOM = 2000

class FaceType(IntEnum):
    SYSTEM = 1
    EMOJI = 2

# 状态机

class StatusSource(IntEnum):
    SCHEDULE = 10       
    INTERACTION = 50    
    LLM_TOOL = 100      

class StatusType(Enum):
    STANDARD = "standard"  
    CUSTOM = "custom"   

# 开发者默认值（调试探针）

class Fallback:
    FACE_ID = 21                  # 可爱
    STATUS = QQStatus.ONLINE
    EXT_STATUS = 0

    ACTIVE_DEFAULT_EXT = 1058     # 元气满满
    BACKGROUND_DEFAULT_EXT = 1060 # 无聊中
    SCHEDULER_DEFAULT_EXT = 1300  # 摸鱼中
    LLM_DEFAULT_EXT = 1052        # 我没事

class Duration:
    INTERACTION_HOOK = 60      # 交互唤醒（临时状态）时长
    LLM_TOOL_SETTING = 2700    # LLM 主动设置状态的最长有效期
    INFINITE = 999999          # 逻辑上的"永久"

class Limits:
    WORDING_LENGTH = 8         # 自定义文字截断长度
    FACE_TYPE_THRESHOLD = 5027 # 大于此 ID 视为 face_type=2 (Emoji)

class Retry:
    MAX_COUNT = 3
    BASE_DELAY = 2.0
    MAX_DELAY = 10.0

class Timing:
    # API 并发安全缓冲
    # 如果后端很脆弱，可以适当调大；如果后端强壮，完全可以移除
    API_CALL_DELAY = 0.05 

    # 状态同步轮询配置
    SYNC_POLL_TIMEOUT = 3.0    # 最长等待
    SYNC_POLL_INTERVAL = 0.5   # 每几秒检查一次

class Cache:
    USER_STATUS_TTL = 180      # 缓存用户状态时长