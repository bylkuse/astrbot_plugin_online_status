import asyncio
import json
import time
from typing import Optional, Tuple, Dict, Any
from astrbot.api import logger

from ..domain import OnlineStatus, StatusType, NapcatExt, Retry, Cache, StatusFactory
from .base import BaseStatusAdapter

class NapcatAdapter(BaseStatusAdapter):
    def __init__(self, client):
        self.client = client
        self.MAX_RETRIES = Retry.MAX_COUNT
        self.BASE_DELAY = Retry.BASE_DELAY
        self.MAX_DELAY = Retry.MAX_DELAY

        self._cached_self_id = None
        self._user_cache = {}
        self.CACHE_TTL = Cache.USER_STATUS_TTL

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    async def _safe_call_api(self, action: str, timeout: float = 5.0, **params) -> Optional[Dict[str, Any]]:
        """[防腐层] 统一处理网络异常、超时、非标响应"""
        if not self.client:
            logger.error(f"[OnlineStatus] ❌ NA: 适配器未连接, 无法调用 {action}")
            return None

        try:
            # 1. 超时控制
            ret = await asyncio.wait_for(
                self.client.api.call_action(action, **params), 
                timeout=timeout
            )

            # 2. 响应清洗
            if ret is None:
                logger.warning(f"[OnlineStatus] 🐧 NA: Napcat {action} 返回 None (可能是网络超时)")
                return None

            if isinstance(ret, dict):
                return ret

            # 3. 容错
            if isinstance(ret, str):
                try:
                    return json.loads(ret)
                except json.JSONDecodeError:
                    return {"status": "unknown", "retcode": -1, "data": ret, "_raw_str": ret}

            logger.warning(f"[OnlineStatus] 🐧 NA: Napcat {action} 返回了未知类型: {type(ret)}")
            return {"status": "unknown", "retcode": -1, "data": ret}

        except asyncio.TimeoutError:
            logger.error(f"[OnlineStatus] ❌ NA: Napcat {action} 调用超时 ({timeout}s)")
            return None
        except Exception as e:
            logger.error(f"[OnlineStatus] ❌ NA: Napcat {action} 调用异常: {e}")
            return None

    # --- 业务逻辑 ---

    async def _get_self_id(self) -> Optional[int]:
        if self._cached_self_id:
            return self._cached_self_id

        ret = await self._safe_call_api("get_login_info")

        if ret:
            data = ret.get("data", ret)
            if isinstance(data, dict) and "user_id" in data:
                try:
                    self._cached_self_id = int(data["user_id"])
                    logger.info(f"[OnlineStatus] 🐧 NA: 成功获取 Bot Self ID: {self._cached_self_id}")
                    return self._cached_self_id
                except ValueError:
                    pass

        logger.warning(f"[OnlineStatus] ❌ NA: 获取自身 ID 失败: {ret}")
        return None

    async def set_custom_status(self, status: OnlineStatus) -> bool:
        action, payload = NapcatSerializer.serialize(status)

        attempt = 0
        current_delay = self.BASE_DELAY

        while attempt < self.MAX_RETRIES:
            attempt += 1

            ret = await self._safe_call_api(action, **payload)
            logger.debug(f"[OnlineStatus] 🐧 NA: Call [{action}] Payload: {payload} | Ret: {str(ret)[:100]}")

            success = False

            if ret:
                if ret.get('status') == 'ok' or ret.get('retcode') == 0:
                    success = True
                elif isinstance(ret.get('_raw_str'), str):
                    raw = ret['_raw_str'].lower()
                    if "success" in raw or "ok" in raw:
                        success = True

            if success:
                logger.debug(f"[OnlineStatus] ✅ NA: 状态同步成功: {status.log_desc}")
                return True

            logger.warning(f"[OnlineStatus] ❌ NA: 状态同步失败 (尝试 {attempt}/{self.MAX_RETRIES})...")

            if attempt < self.MAX_RETRIES:
                await asyncio.sleep(current_delay)
                current_delay = min(current_delay * 2, self.MAX_DELAY)
            else:
                # 回查
                if await self._verify_status_match(status):
                    logger.info("[OnlineStatus] ✅ NA: 状态同步实际上已生效 (回查通过)")
                    return True

        return False

    async def get_user_status(self, user_id: int, use_cache: bool = True) -> Optional[OnlineStatus]:
        now = time.time()

        # 1. 读缓存
        if use_cache and user_id in self._user_cache:
            data, expire = self._user_cache[user_id]
            if now < expire:
                return data
            else:
                del self._user_cache[user_id]

        # 2. 安全 API 调用
        await asyncio.sleep(0.05) 

        ret = await self._safe_call_api('nc_get_user_status', user_id=user_id)

        if ret and isinstance(ret, dict):
            data_payload = ret.get("data", ret)

            status_obj = StatusFactory.from_napcat_payload(data_payload)

            self._user_cache[user_id] = (status_obj, now + self.CACHE_TTL)
            return status_obj

        return None

    async def _verify_status_match(self, target_status: OnlineStatus) -> bool:
        """回查校验"""
        try:
            self_id = await self._get_self_id()
            if not self_id: return False

            await asyncio.sleep(1.0)

            # 无视缓存
            current = await self.get_user_status(self_id, use_cache=False)
            if not current: return False

            # 对比
            if target_status.type == StatusType.STANDARD:
                return (current.status == target_status.status and 
                        current.ext_status == target_status.ext_status)

            elif target_status.type == StatusType.CUSTOM:
                # 注：Napcat 无法查询到具体的 wording，只能查到 ext_status=2000
                if current.ext_status == NapcatExt.CUSTOM: 
                    return True

            return False
        except Exception as e:
            logger.warning(f"[OnlineStatus] ❌ NA: [回查校验] 执行异常: {e}")
            return False


class NapcatSerializer:
    @staticmethod
    def serialize(status: OnlineStatus) -> Tuple[str, Dict[str, Any]]:
        if status.type == StatusType.CUSTOM:
            payload = {
                "face_id": status.face_id,
                "face_type": status.face_type,
                "wording": status.wording
            }
            return 'set_diy_online_status', payload
        else:
            payload = {
                "status": status.status,
                "ext_status": status.ext_status,
                "battery_status": status.battery_status
            }
            return 'set_online_status', payload