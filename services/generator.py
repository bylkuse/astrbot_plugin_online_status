import json
import re
import os
import time
from datetime import date
from typing import List, Dict, Optional

from astrbot.api import logger

from ..adapters.astr import AstrHost
from ..utils.config import PluginConfig

class ScheduleGenerator:
    def __init__(self, host: AstrHost, config: PluginConfig, data_dir: str):
        self.host = host
        self.config = config
        self.data_dir = data_dir

    async def generate_daily_schedule(self, target_date: date) -> List[Dict]:
        """
        生成指定日期的日程表
        :return: JSON List 或 []
        """
        weekday_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
        weekday_str = weekday_map.get(target_date.weekday(), "未知")
        
        # 1. 准备 Prompt 素材
        # 状态列表 (包含标准和自定义预设)
        status_list_str = self.config.get_status_list_prompt_str()
        # 表情列表 (纯 Face ID 映射)
        face_list_str = self.config.get_face_list_prompt_str()
        
        persona_text = await self.host.get_persona_prompt()
        
        # 2. 构建 System Prompt
        sys_template = self.config.system_prompt
        
        # 如果用户配置里保留了占位符，先清空，避免重复
        sys_prompt = sys_template.replace("{status_list}", "").replace("{face_list}", "")
        sys_prompt = sys_prompt.replace("{persona}", persona_text)
        
        # 3. 构建 User Prompt (明确任务与边界)
        # [优化] 这里显式定义两个“资源池”，并强制绑定字段来源
        user_prompt = (
            f"今天是 {target_date.isoformat()} ({weekday_str})。\n"
            "请生成今天的作息时间表 JSON。\n"
            "\n"
            "### 📚 可用资源池 (必须严格从中选择)\n"
            "**[POOL A] 完整状态预设 (用于 `status_name` 字段)**\n"
            f"{status_list_str}\n"
            "\n"
            "**[POOL B] 纯图标/表情 (仅用于 `face_name` 字段)**\n"
            f"[{face_list_str}]\n"
            "\n"
            "### ⚠️ 生成规则 (Strict Mode)\n"
            "1. **模式一 (推荐)**: 调用预设。\n"
            "   - 使用 `status_name` 字段。\n"
            "   - 值必须 **严格相等** 地选自 [POOL A]。\n"
            "   - 不需要 `text`, `face_name`, `is_silent` 字段。\n"
            "\n"
            "2. **模式二 (自定义)**: 编写自定义文字。\n"
            "   - **禁止** 使用 `status_name` 字段。\n"
            "   - 必须包含 `text` (自定义显示的文字，不超过8个任意字符)。\n"
            "   - 必须包含 `face_name`: 值必须选自 [POOL B] (图标)。\n"
            "   - 必须包含 `is_silent`: true (勿扰/睡觉) 或 false (活跃/可聊)。\n"
            "\n"
            "### 格式示例\n"
            "[\n"
            '  {"start": "08:00", "end": "09:00", "status_name": "在线"},           <-- 模式一：从 POOL A 选取\n'
            '  {"start": "09:00", "end": "12:00", "status_name": "写Bug"},          <-- 模式一：从 POOL A 选取\n'
            '  {"start": "12:00", "end": "13:00", "text": "干饭!", "face_name": "饥饿", "is_silent": false} <-- 模式二：face_name 从 POOL B 选取\n'
            "]\n"
            "注意：只返回 JSON，不要包含 Markdown 标记。"
        )

        # 4. 调用 LLM
        gen_config = self.config.generation_config
        
        raw_text = await self.host.llm_generate_text(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            config=gen_config
        )
        
        if not raw_text:
            return []

        # 5. 解析并校验 JSON
        try:
            clean_text = self._clean_json_str(raw_text)
            schedule_data = json.loads(clean_text)
            
            if isinstance(schedule_data, list):
                # [新增] 简单的后处理校验，防止 LLM 胡乱填
                valid_data = []
                for item in schedule_data:
                    # 修正：如果 LLM 既填了 status_name 又填了 text，优先信 status_name
                    if item.get("status_name") and item.get("text"):
                        # 清理掉自定义字段，强制走预设逻辑
                        item.pop("text", None)
                        item.pop("face_name", None)
                    valid_data.append(item)
                    
                logger.info(f"成功生成日程，包含 {len(valid_data)} 个时间段。")
                return valid_data
            else:
                logger.warning(f"LLM 返回的不是 List 格式: {type(schedule_data)}")
                return []
                
        except json.JSONDecodeError as e:
            timestamp = int(time.time())
            filename = f"error_llm_json_{target_date}_{timestamp}.txt"
            filepath = os.path.join(self.data_dir, filename)
            
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write("=== LLM Raw Response ===\n")
                    f.write(raw_text)
                    f.write("\n\n=== Error Details ===\n{str(e)}")
                logger.error(f"❌ LLM JSON 解析失败。日志已保存: {filepath}")
            except Exception:
                pass
            return []
        except Exception as e:
            logger.error(f"日程数据处理异常: {e}")
            return []

    def _clean_json_str(self, text: str) -> str:
        """移除 Markdown 代码块标记，提取 JSON 内容"""
        text = text.strip()
        pattern = r"```(?:json)?\s*(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text