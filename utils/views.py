import time
from typing import Optional

from ..domain import OnlineStatus, StatusSource
from ..utils import PluginConfig

class StatusView:
    def __init__(self, config: PluginConfig):
        self.config = config

    # --- 业务消息 ---

    def render_self_awareness(self, current: OnlineStatus, background: OnlineStatus) -> str:
        """渲染自身状态感知提示词"""
        duration_desc = self._format_duration(time.time() - background.created_at)

        if current.source == StatusSource.INTERACTION:
            # 打断/唤醒模式
            tpl = self.config.get_template("self_awareness_interruption")
            if not tpl: # 兜底默认值
                tpl = "\n\n# 自身状态感知\n* 在此刻之前，你已经维持“{bg_status_name}”状态达 {duration}\n* 用户发消息打断了你的原计划/状态，你临时切换到了“{current_status_name}”状态来回应\n* 在回复中体现这种时序变化"
            
            return tpl.format(
                bg_status_name=background.wording or "在线",
                current_status_name=current.wording,
                duration=duration_desc
            )
        else:
            tpl = self.config.get_template("self_awareness_immersion")
            if not tpl:
                tpl = "\n\n# 自身状态感知\n* 你已经维持“{status_name}”状态达 {duration}\n* 你的回复应当符合当前正在进行的活动或心情"

            return tpl.format(
                status_name=background.wording or current.wording,
                duration=duration_desc
            )

    def render_user_awareness(self, user_id: int, status_name: str) -> str:
        """渲染用户状态感知提示词"""
        tpl = self.config.get_template("user_awareness")
        if not tpl:
            tpl = "\n\n# 对象状态感知\n* 和你对话的用户 (QQ:{user_id}) 当前的状态是“{user_status_name}”\n* 关注对方的状态，注意调整你的语气和话题"

        return tpl.format(user_id=user_id, user_status_name=status_name)

    def render_tool_instruction(self, authorized: bool) -> str:
        """渲染工具调用指引"""
        if not authorized:
            return self.config.get_template(
                "tool_instruction_denied", 
                "\n\n[System Instruction]\nPlease ignore the tool `update_qq_status`. You are NOT authorized."
            )

        tpl = self.config.get_template("tool_instruction_authorized")
        # 兜底
        if not tpl:
            return "\n\n# 维护&更新社交状态\n* 拥有权限，可调用 `update_qq_status` 修改状态。"

        # 动态注入列表
        status_list = self.config.get_status_list_prompt_str()
        face_list = self.config.get_face_list_prompt_str()

        return tpl.replace("{status_list}", status_list).replace("{face_list}", face_list)

    # --- 调试消息 ---

    def render_tool_response(self, status_name: str, custom_text: Optional[str] = None) -> str:
        """LLM 工具调用成功后的系统提示"""
        desc = f"status='{status_name}'"
        if custom_text:
            desc += f", custom_text='{custom_text}'"
        return f"[System: 你的在线状态已更新 ({desc}). 保持人设自然回复.]"

    def render_query_result(self, user_id: int, status_obj: OnlineStatus) -> str:
        """渲染查询结果"""
        return (
            f"用户 {user_id} 当前状态:\n"
            f"----------------\n"
            f"🏷️ 主状态: {status_obj.status}\n"
            f"🧩 扩展ID: {status_obj.ext_status}\n"
        )

    def render_simulation_result(self, current: OnlineStatus, remaining_time: Optional[int] = None) -> str:
        """渲染唤醒模拟结果"""
        desc = (
            f"✅ 已模拟消息交互。\n"
            f"----------------\n"
            f"当前状态: {current.wording}\n"
            f"类型: {current.type.name}\n"
            f"来源: {current.source.name}\n"
            f"专注: {current.is_silent}"
        )
        if remaining_time is not None:
            desc += f"\n剩余时间: {remaining_time}s"
        return desc

    def render_persona_debug(self, current_id: str, main_id: str) -> str:
        """渲染权限诊断信息"""
        is_match = (current_id == main_id)
        return (
            f"🕵️‍♂️ 人格权限诊断 (Persona Debug)\n"
            f"=============================\n"
            f"🔹 Event.persona_id (当前): {repr(current_id)}\n"
            f"🔸 Host.main_id     (预设): {repr(main_id)}\n"
            f"=============================\n"
            f"⚖️ 匹配结果: {'✅ 通过' if is_match else '❌ 拒绝'}\n"
        )

    # --- 辅助方法 ---

    def _format_duration(self, seconds: float) -> str:
        m = int(seconds / 60)
        if m < 1: return "刚刚"
        if m < 60: return f"{m}分钟"
        h = m // 60
        m = m % 60
        return f"{h}小时{m}分"