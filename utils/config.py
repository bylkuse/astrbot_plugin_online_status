from dataclasses import dataclass
from typing import Dict, List, Optional

from astrbot.api import AstrBotConfig, logger

@dataclass
class StatusPresetItem: # 原 StatusConfigItem
    name: str
    status_id: int
    ext_status_id: int
    is_silent: bool

@dataclass
class CustomPresetItem:
    name: str
    face_id: int
    wording: str
    is_silent: bool
    face_type: int = 1

@dataclass
class FacePresetItem:
    name: str
    face_type: int
    face_id: int


class PluginConfig:
    def __init__(self, config: AstrBotConfig):
        self._raw_config = config
        self.status_presets: Dict[str, StatusPresetItem] = {}
        self.custom_presets: Dict[str, CustomPresetItem] = {}
        self.face_presets: Dict[str, FacePresetItem] = {}
        self._load_all_presets()

    def _load_all_presets(self):
        # 1. 加载标准状态
        self.status_presets = {}
        for item_str in self._raw_config.get("status_presets", []):
            parts = item_str.replace("，", ",").strip().split(",")
            if len(parts) < 4: continue
            try:
                item = StatusPresetItem(parts[0].strip(), int(parts[1]), int(parts[2]), parts[3].strip().lower() in ["true", "1", "yes", "是"])
                self.status_presets[item.name] = item
            except ValueError: continue
        
        # 2. 加载自定义状态
        self.custom_presets = {}
        for item_str in self._raw_config.get("custom_presets", []):
            parts = item_str.replace("，", ",").strip().split(",")
            if len(parts) < 4: continue
            try:
                # 格式: 名称, face_id, wording, is_silent
                item = CustomPresetItem(parts[0].strip(), int(parts[1]), parts[2].strip(), parts[3].strip().lower() in ["true", "1", "yes", "是"])
                self.custom_presets[item.name] = item
            except ValueError: continue

        # 3. 加载表情映射
        self.face_presets = {}
        for item_str in self._raw_config.get("face_presets", []):
            parts = item_str.replace("，", ",").strip().split(",")
            if len(parts) < 3: continue
            try:
                # 格式: 名称, face_type, face_id
                item = FacePresetItem(parts[0].strip(), int(parts[1]), int(parts[2]))
                self.face_presets[item.name] = item
            except ValueError: continue

        logger.info(f"[OnlineStatus] Loaded {len(self.status_presets)} standard, {len(self.custom_presets)} custom, {len(self.face_presets)} face presets.")

    # ---------------------------------------------------------
    # 新增：属性访问器
    # ---------------------------------------------------------

    @property
    def main_persona_id(self) -> str:
        """
        获取配置的主人格 ID
        对应 _conf_schema.json 中的 "main_persona_id"
        """
        # 默认为空字符串 (即未指定)
        return self._raw_config.get("main_persona_id", "")

    @property
    def system_prompt(self) -> str:
        return self._raw_config.get("system_prompt_template", "")

    @property
    def debug_enabled(self) -> bool:
        """是否启用调试指令"""
        return self._raw_config.get("enable_debug_cmd", False)

    @property
    def generation_config(self) -> dict:
        """获取 LLM 生成相关的子配置"""
        return self._raw_config.get("generation_config", {})

    # ---------------------------------------------------------
    # 辅助方法
    # ---------------------------------------------------------

    def get_status_list_prompt_str(self) -> str:
        """为 LLM 生成可用状态列表 (合并标准和自定义)"""
        lines = ["- " + name for name in self.status_presets.keys()]
        lines += ["- " + name for name in self.custom_presets.keys()]
        return "\n".join(lines)
    
    def get_face_list_prompt_str(self) -> str:
        """为 LLM 生成可用表情列表"""
        return ", ".join(self.face_presets.keys())

    def get_preset(self, name: str):
        """
        统一查找预设，优先查找自定义预设，其次标准预设
        """
        if name in self.custom_presets:
            return self.custom_presets[name]
        if name in self.status_presets:
            return self.status_presets[name]
        return None