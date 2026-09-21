from __future__ import annotations
import json
import heapq
import re
import uuid
from pathlib import Path
from PIL import Image
from .imaging import image_hash, load_image_bytes, save_png
from .schema import ID_RE

OUTPUT_FOLDERS = {"generate": "t2i", "edit": "i2i", "local": "fix2i"}

OUTPUT_RE = re.compile(r"^[0-9a-f]{32}_(?:[0-9]{2}|[1-9][0-9]{2})(?:_generated)?\.(?:png|json)$")

def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

class Store:
    def __init__(self, data: Path):
        self.data = data
        self.uploads = data / "uploads"
        self.outputs = data / "outputs"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.outputs.mkdir(parents=True, exist_ok=True)
        for folder in OUTPUT_FOLDERS.values():
            (self.outputs / folder).mkdir(exist_ok=True)

    def upload(self, raw: bytes, mask: bool = False) -> dict:
        image = load_image_bytes(raw, mask=mask)
        image_id = uuid.uuid4().hex
        path = self.upload_path(image_id, must_exist=False)
        save_png(image, path)
        return {"id": image_id, "width": image.width, "height": image.height,
                "mode": image.mode, "sha256": image_hash(path),
                "url": f"/api/uploads/{image_id}.png"}

    def upload_path(self, image_id: str, must_exist: bool = True) -> Path:
        if not ID_RE.fullmatch(image_id):
            raise ValueError("画像IDが不正です。")
        path = self.uploads / (image_id + ".png")
        if must_exist and not path.is_file():
            raise FileNotFoundError("参照画像がありません。画像をもう一度追加してください。")
        return path

    def open_upload(self, image_id: str) -> Image.Image:
        with Image.open(self.upload_path(image_id)) as image:
            image.load()
            return image.copy()

    def output_path(self, name: str, must_exist: bool = True, mode: str | None = None) -> Path:
        if not OUTPUT_RE.fullmatch(name):
            raise ValueError("出力名が不正です。")
        if mode is not None:
            path = self.outputs / OUTPUT_FOLDERS[mode] / name
        else:
            path = self.outputs / name
            if not path.is_file():
                for folder in OUTPUT_FOLDERS.values():
                    candidate = self.outputs / folder / name
                    if candidate.is_file():
                        path = candidate
                        break
        if must_exist and not path.is_file():
            raise FileNotFoundError("出力ファイルが見つかりません。")
        return path

    def history(self, limit: int = 80) -> list[dict]:
        out = []
        # bounded list, newest first; metadata, not full images, is read.
        def candidates():
            for folder in [self.outputs, *(self.outputs / name for name in OUTPUT_FOLDERS.values())]:
                for path in folder.glob("*.json"):
                    try:
                        yield path.stat().st_mtime, str(path), path
                    except OSError:
                        continue  # An output may have been moved/deleted during enumeration.
        paths = heapq.nlargest(max(0, limit), candidates())
        for _, _, path in paths:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(row, dict) or not isinstance(row.get("file"), str):
                    continue
                request = row.get("request")
                if not isinstance(request, dict) or not isinstance(request.get("prompt"), str):
                    continue
                if self.output_path(row["file"], must_exist=False).is_file():
                    out.append(row)
            except (OSError, ValueError, KeyError):
                continue
        return out
