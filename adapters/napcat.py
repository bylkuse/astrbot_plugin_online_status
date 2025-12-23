import asyncio
import json
from typing import Optional
from astrbot.api import logger

from ..domain import OnlineStatus, StatusType
from .base import BaseStatusAdapter

class NapcatAdapter(BaseStatusAdapter):
    def __init__(self, client):
        self.client = client  # Client 实例
        # 配置重试参数
        self.MAX_RETRIES = 3      # 最大重试次数
        self.BASE_DELAY = 2.0     # 初始延迟(秒)
        self.MAX_DELAY = 10.0     # 最大延迟上限
        self._cached_self_id = None

    def get_platform_name(self) -> str:
        return "aiocqhttp" # 对应 AstrBot 的 OneBot11 平台名

    async def _get_self_id(self) -> Optional[int]:
        if self._cached_self_id:
            return self._cached_self_id
        try:
            # 标准 OneBot11 获取登录号接口
            ret = await self.client.api.call_action("get_login_info")
            if ret and isinstance(ret, dict):
                data = ret.get("data", ret)
                if isinstance(data, dict) and "user_id" in data:
                    self._cached_self_id = int(data["user_id"])
                    logger.info(f"[Napcat] 成功获取 Bot Self ID: {self._cached_self_id}")
                    return self._cached_self_id
            logger.warning(f"[Napcat] get_login_info 响应异常: {ret}")
        except Exception as e:
            logger.warning(f"[Napcat] 获取自身 ID 异常: {e}")
        return None

    async def _verify_status_match(self, target_status: OnlineStatus) -> bool:
        """
        [内部方法] 回查校验：检查当前 Bot 的实际状态是否与目标状态一致
        仅用于“标准状态”的校验，自定义状态因涉及 wording 比较较为复杂，暂略
        """
        if target_status.type != StatusType.STANDARD:
            return False

        try:
            # 1. 获取 Bot 自身 QQ 号 (uin)
            self_id = await self._get_self_id()
            if not self_id:
                logger.warning("[Napcat] 无法获取自身 UIN，跳过回查校验。")
                return False

            # 2. 查询当前状态
            # 给一点点延迟，让状态同步到服务器
            await asyncio.sleep(1.0) 
            current = await self.get_user_status(self_id)
            
            if not current:
                return False

            # 3. 对比逻辑 (仅对比核心 ID)
            # 注意：QQ 有时会自动把 battery_status 变动，所以只比对 status 和 ext_status
            is_match = (
                current.status == target_status.status and 
                current.ext_status == target_status.ext_status
            )

            if is_match:
                logger.info(f"✅ [回查校验] 验证成功！当前状态已更新为: {current.status}/{current.ext_status}")
                return True
            else:
                logger.warning(
                    f"[回查校验] 状态不匹配。\n"
                    f"预期: {target_status.status} (ext: {target_status.ext_status})\n"
                    f"实际: {current.status} (ext: {current.ext_status})"
                )
                return False

        except Exception as e:
            logger.warning(f"[回查校验] 执行异常: {e}")
            return False

    async def set_custom_status(self, status: OnlineStatus) -> bool:
        action = status.get_api_endpoint()
        payload = status.get_payload()
        
        attempt = 0
        current_delay = self.BASE_DELAY

        while attempt < self.MAX_RETRIES:
            try:
                attempt += 1
                
                # 1. 发起调用
                ret = await self.client.api.call_action(action, **payload)

                # ================= DEBUG PROBE START =================
                log_prefix = f"[DEBUG PROBE][{action}]"
                try:
                    ret_dump = json.dumps(ret, ensure_ascii=False, indent=2) if ret is not None else "None"
                    logger.warning(f"{log_prefix} 响应内容:\n{ret_dump}")
                except Exception:
                    logger.warning(f"{log_prefix} 原始响应: {ret}")
                # ================== DEBUG PROBE END ==================

                # 2. 判定逻辑 A: 明确成功
                if ret and isinstance(ret, dict):
                    if ret.get('status') == 'ok' and ret.get('retcode') == 0:
                        logger.info(f"✅ 状态同步成功: {status.log_desc}")
                        return True
                    else:
                        logger.warning(f"❌ API 显式拒绝: {ret}")
                
                # 3. 判定逻辑 B: 无响应/超时 -> 触发回查校验
                elif ret is None:
                    logger.warning(f"⚠️ API 无响应 (None)。尝试执行回查校验...")
                    
                    # 仅针对标准状态进行 ID 校验，避免自定义文字匹配的复杂性
                    if status.type == StatusType.STANDARD:
                        if await self._verify_status_match(status):
                            # 校验通过，视为成功，直接返回
                            return True
                    else:
                        logger.warning("当前为自定义状态，暂不支持自动回查校验，将继续重试。")

                # 若代码执行到此，说明 本次尝试失败 且 校验未通过

            except Exception as e:
                logger.error(f"❌ 调用 Napcat 接口异常: {str(e)}")

            # 4. 重试逻辑
            if attempt < self.MAX_RETRIES:
                logger.warning(f"[Napcat] 设置未确认，将在 {current_delay}秒 后重试 ({attempt}/{self.MAX_RETRIES})...")
                await asyncio.sleep(current_delay)
                current_delay = min(current_delay * 2, self.MAX_DELAY)
            else:
                logger.error(f"[Napcat] ❌ 达到最大重试次数 ({self.MAX_RETRIES})。")
                
                # 最后一次挣扎：如果重试都耗尽了，最后再查一次，万一它是真的慢呢？
                if ret is None and status.type == StatusType.STANDARD:
                    logger.warning("[Napcat] 最终回查校验...")
                    if await self._verify_status_match(status):
                        return True
                        
        return False

    async def get_user_status(self, user_id: int) -> Optional[OnlineStatus]:
        """
        实现基类方法：获取用户状态
        """
        try:
            ret = await self.client.api.call_action(
                'nc_get_user_status', 
                user_id=user_id
            )

            # ret 结构预期: { status: 10, ext_status: 1028, ... }
            if ret and isinstance(ret, dict):
                return OnlineStatus.from_napcat_data(ret)
            
            return None
        except Exception as e:
            logger.error(f"获取用户 {user_id} 状态失败: {e}")
            return None