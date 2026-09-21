from __future__ import annotations
import hashlib
import io
import json
import warnings
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps, PngImagePlugin

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 32_000_000
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "BMP"}


def load_image_bytes(raw: bytes, *, mask: bool = False) -> Image.Image:
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("画像は空でない32 MiB以下のファイルにしてください。")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(raw)) as src:
                if src.format not in ALLOWED_FORMATS:
                    raise ValueError("PNG / JPEG / WebP / BMPに対応しています。")
                if src.width * src.height > MAX_PIXELS or max(src.size) > 16384:
                    raise ValueError("画像が大きすぎます。3200万画素以下に縮小してください。")
                if getattr(src, "n_frames", 1) > 1:
                    raise ValueError("アニメーションではなく静止画像を指定してください。")
                src.load()
                image = ImageOps.exif_transpose(src).convert("L" if mask else "RGBA")
                image.info.clear()  # Never copy GPS/EXIF or upstream prompt metadata into uploads.
                return image
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as e:
            raise ValueError("展開後の画像が大きすぎます。") from e


def save_png(image: Image.Image, path: Path, metadata: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    info = PngImagePlugin.PngInfo()
    if metadata is not None:
        info.add_itxt("cvELD_Image_Studio", json.dumps(metadata, ensure_ascii=False))
    tmp = path.with_suffix(".partial")
    image.save(tmp, format="PNG", pnginfo=info, compress_level=4)
    tmp.replace(path)


def image_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def mask_is_empty(mask: Image.Image) -> bool:
    return mask.convert("L").getextrema()[1] == 0


def preserve_outside(base: Image.Image, generated: Image.Image, mask: Image.Image,
                     feather: int = 0) -> Image.Image:
    """Composite on the ORIGINAL pixel grid. Black mask pixels remain exact (feather=0)."""
    if mask.size != base.size:
        raise ValueError("マスクと元画像のピクセル寸法が一致しません。")
    base = base.convert("RGBA")
    generated = generated.convert("RGBA").resize(base.size, Image.Resampling.LANCZOS)
    m = mask.convert("L")
    if feather:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    return Image.composite(generated, base, m)
