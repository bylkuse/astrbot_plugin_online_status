from abc import ABC, abstractmethod
from typing import Optional

from ..domain.schemas import OnlineStatus

class BaseStatusAdapter(ABC):
    """
    状态设置适配器基类
    """
    @abstractmethod
    async def set_custom_status(self, status: OnlineStatus) -> bool:
        """
        统一的设置自定义状态接口
        :param status: OnlineStatus 实例
        :return: 是否设置成功
        """
        pass

    @abstractmethod
    def get_platform_name(self) -> str:
        """返回适配器对应的平台名称"""
        pass

    @abstractmethod
    async def get_user_status(self, user_id: int) -> Optional[OnlineStatus]:
        """
        获取指定用户的在线状态
        :param user_id: 目标QQ号
        :return: OnlineStatus 对象或 None (获取失败)
        """
        pass