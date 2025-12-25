from dataclasses import dataclass
from typing import Dict, Optional

from astrbot.api import AstrBotConfig, logger

@dataclass
class StatusPresetItem:
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

@dataclass
class FacePresetItem:
    name: str
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
                item = CustomPresetItem(
                    name=parts[0].strip(), 
                    face_id=int(parts[1]), 
                    wording=parts[2].strip(), 
                    is_silent=parts[3].strip().lower() in ["true", "1", "yes", "是"]
                )
                self.custom_presets[item.name] = item
            except ValueError: continue

        # 3. 加载表情映射
        self.face_presets = {}
        for item_str in self._raw_config.get("face_presets", []):
            parts = item_str.replace("，", ",").strip().split(",")
            if len(parts) < 2: continue
            try:
                # 兼容性处理
                name = parts[0].strip()
                if len(parts) >= 3:
                    face_id = int(parts[2])
                else:
                    face_id = int(parts[1])

                item = FacePresetItem(name, face_id)
                self.face_presets[item.name] = item
            except ValueError: continue

        logger.info(f"[OnlineStatus] 📄 PC: Loaded {len(self.status_presets)} standard, {len(self.custom_presets)} custom, {len(self.face_presets)} face presets.")

    @property
    def main_persona_id(self) -> str:
        return self._raw_config.get("main_persona_id", "")

    @property
    def wake_up_status(self) -> str:
        return self._raw_config.get("wake_up_status", "在线")

    @property
    def system_prompt(self) -> str:
        return self._raw_config.get("system_prompt_template", "")

    @property
    def prompt_templates(self) -> dict:
        return self._raw_config.get("prompt_templates", {})

    def get_template(self, key: str, default: str = "") -> str:
        return self.prompt_templates.get(key, default)

    @property
    def generation_config(self) -> dict:
        return self._raw_config.get("generation_config", {})

    # ---------------------------------------------------------
    # 辅助方法
    # ---------------------------------------------------------

    def get_status_list_prompt_str(self) -> str:
        lines = ["- " + name for name in self.status_presets.keys()]
        lines += ["- " + name for name in self.custom_presets.keys()]
        return "\n".join(lines)

    def get_face_list_prompt_str(self) -> str:
        return ", ".join(self.face_presets.keys())

    def get_preset(self, name: str):
        """查找预设:优先自定义，其次标准预设"""
        if name in self.custom_presets:
            return self.custom_presets[name]
        if name in self.status_presets:
            return self.status_presets[name]
        return None

    def get_status_name_by_ids(self, status_id: int, ext_status_id: int) -> Optional[str]:
        """反查预设名"""
        for name, item in self.status_presets.items():
            if item.status_id == status_id and item.ext_status_id == ext_status_id:
                return name

        # 兜底
        if ext_status_id == 0:
            for name, item in self.status_presets.items():
                if item.status_id == status_id and item.ext_status_id == 0:
                    return name

        return None