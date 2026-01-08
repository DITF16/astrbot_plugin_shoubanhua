import json
from pathlib import Path
from typing import Dict, List, Tuple


class PresetManager:
    def __init__(self, data_dir: Path):
        self.file_path = data_dir / "presets.json"
        self.presets: Dict[str, str] = {}
        self._load()

    def _load(self):
        """加载预设文件，如果不存在则创建默认值"""
        if not self.file_path.exists():
            # 默认预设，作为初始化文件
            default_presets = {
                "手办化": "Use the nano-banana model to create a 1/7 scale commercialized figure of the character in the illustration, in a realistic style and environment. Place the figure on a computer desk, using a circular transparent acrylic base without any text. On the computer screen, display the ZBrush modeling process of the figure. Next to the computer screen, place a BANDAI-style toy packaging box printed with the original artwork.",
                "Q版化": "Transform the character into a Nendoroid style Chibi figure. Big head, small body, cute proportions, smooth plastic texture, 3D rendering style.",
                "痛屋化": "Transform the room into an otaku's paradise, filled with anime posters, figurines, and merchandise. Colorful LED lighting, messy but cozy atmosphere."
            }
            self.save_all(default_presets)

        try:
            content = self.file_path.read_text(encoding="utf-8")
            self.presets = json.loads(content)
        except Exception as e:
            print(f"Error loading presets: {e}")
            self.presets = {}

    def save_all(self, data: Dict[str, str]):
        """保存所有预设"""
        self.presets = data
        try:
            self.file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"Error saving presets: {e}")

    def get_prompt(self, key: str) -> str:
        """获取提示词"""
        return self.presets.get(key, "")

    def add_prompt(self, key: str, prompt: str):
        """添加或修改预设"""
        self.presets[key] = prompt
        self.save_all(self.presets)

    def delete_prompt(self, key: str) -> bool:
        """删除预设"""
        if key in self.presets:
            del self.presets[key]
            self.save_all(self.presets)
            return True
        return False

    def get_all(self) -> List[Tuple[str, str]]:
        """获取所有预设 (key, prompt)"""
        return sorted(self.presets.items())