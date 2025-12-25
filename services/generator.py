import json
import re
import time
import asyncio
from datetime import date
from typing import List, Dict, Union
from pathlib import Path

from pydantic import ValidationError
from astrbot.api import logger

from ..adapters import AstrHost
from ..utils import PluginConfig
from ..domain import ScheduleItem

class ScheduleGenerator:
    def __init__(self, host: AstrHost, config: PluginConfig, data_dir: Union[str, Path]):
        self.host = host
        self.config = config
        self.data_dir = Path(data_dir)

    async def generate_daily_schedule(self, target_date: date) -> List[Dict]:
        """生成日程表"""
        weekday_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
        weekday_str = weekday_map.get(target_date.weekday(), "未知")

        # 1. 准备 Prompt
        status_list_str = self.config.get_status_list_prompt_str()
        face_list_str = self.config.get_face_list_prompt_str()
        persona_text = await self.host.get_persona_prompt()

        sys_template = self.config.system_prompt
        sys_prompt = sys_template.replace("{status_list}", status_list_str)
        sys_prompt = sys_prompt.replace("{face_list}", face_list_str)
        sys_prompt = sys_prompt.replace("{persona}", persona_text)

        user_prompt = (
            f"今天是 {target_date.isoformat()} ({weekday_str})。\n"
            "请根据上述规则，生成今天的作息时间表 JSON。"
        )

        # 2. 调用 LLM
        gen_config = self.config.generation_config
        raw_text = await self.host.llm_generate_text(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            config=gen_config
        )

        if not raw_text:
            return []

        # 3. 解析并校验
        try:
            clean_text = self._clean_json_str(raw_text)
            parsed_data = json.loads(clean_text)

            raw_list = []

            # 根节点即列表
            if isinstance(parsed_data, list):
                raw_list = parsed_data
            
            # 列表在内部
            elif isinstance(parsed_data, dict):
                found = False
                # 优先找特定 key
                for key in ["schedule", "timeline", "data", "items", "activities", "tasks"]:
                    if key in parsed_data and isinstance(parsed_data[key], list):
                        raw_list = parsed_data[key]
                        found = True
                        break
                # 第一个 list
                if not found:
                    for val in parsed_data.values():
                        if isinstance(val, list):
                            raw_list = val
                            found = True
                            break
            if not raw_list:
                logger.warning("[OnlineStatus] 🚀 SG: LLM 返回结果中未找到有效列表")
                return []

            valid_data = []
            for item in raw_list:
                try:
                    # 验证并清洗
                    model = ScheduleItem.model_validate(item)
                    valid_data.append(model.model_dump(exclude_none=True))
                except ValidationError:
                    continue

            if not valid_data:
                logger.warning("[OnlineStatus] 🚀 SG: 数据清洗后有效日程为空")
                return []

            logger.info(f"[OnlineStatus] ✅ SG: 成功生成日程，包含 {len(valid_data)} 个时间段")
            return valid_data

        except Exception as e:
            asyncio.create_task(self._dump_error_log_async(target_date, raw_text, e))
            return []

    def _clean_json_str(self, text: str) -> str:
        text = text.strip()
        pattern = r"```(?:json)?\s*(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        else:
            first_bracket = re.search(r"[\[\{]", text)
            last_bracket = re.search(r"[\]\}]", text[::-1])
            if first_bracket and last_bracket:
                start = first_bracket.start()
                end = len(text) - last_bracket.start()
                text = text[start:end]
        return text

    async def _dump_error_log_async(self, target_date, raw_text, error):
        def _write():
            timestamp = int(time.time())
            filename = f"error_llm_json_{target_date}_{timestamp}.txt"
            filepath = self.data_dir / filename

            content = (
                "=== LLM Raw Response ===\n"
                f"{str(raw_text)}\n\n"
                "=== Error Details ===\n"
                f"{str(error)}"
            )

            try:
                filepath.write_text(content, encoding='utf-8')

            except Exception as e:
                logger.error(f"[OnlineStatus] ❌ SG: 无法写入调试日志文件 [{filepath}]: {e}")

        asyncio.get_running_loop().run_in_executor(None, _write)