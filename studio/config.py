from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "Qwen/Qwen-Image-2.1"
MODEL_REVISION = "790c92633540aa0cb11d9abf19eb46d861714758"
DIFFUSERS_REVISION = "6256aa7666cedd47443adc8f82da9a10e110b09c"

@dataclass(frozen=True)
class Config:
    root: Path
    model_dir: Path
    data_dir: Path
    port: int = 7860
    gpu_index: int = 0
    open_browser: bool = True
    text_cache_mb: int = 96

    @classmethod
    def load(cls, root: Path = ROOT) -> "Config":
        path = root / "config.json"
        raw = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        def local_path(key: str, fallback: str) -> Path:
            p = Path(raw.get(key, fallback)).expanduser()
            return (root / p).resolve() if not p.is_absolute() else p.resolve()
        port = int(raw.get("port", 7860))
        gpu = int(raw.get("gpu_index", 0))
        if not 1024 <= port <= 65535 or not 0 <= gpu <= 15:
            raise ValueError("config.json: port は1024〜65535、gpu_index は0〜15にしてください。")
        return cls(root.resolve(), local_path("model_dir", "models/Qwen-Image-2.1"),
                   local_path("data_dir", "data"), port, gpu,
                   bool(raw.get("open_browser", True)),
                   max(0, min(512, int(raw.get("text_cache_mb", 96)))))

    def prepare(self) -> None:
        for p in (self.data_dir / "uploads", self.data_dir / "outputs", self.root / "logs",
                  self.root / ".runtime"):
            p.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(self.root / ".runtime" / "hf"))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("DO_NOT_TRACK", "1")
