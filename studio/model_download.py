from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path, PurePosixPath
from .config import Config, MODEL_ID, MODEL_REVISION
from .store import write_json

PARTS = {"processor", "scheduler", "text_encoder", "transformer", "vae"}
ROOT_FILES = {"model_index.json", "LICENSE", "README.md"}

def wanted(name: str) -> bool:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name:
        return False
    if name in ROOT_FILES:
        return True
    return len(p.parts) > 1 and p.parts[0] in PARTS and p.suffix in {".json", ".safetensors", ".txt", ".model", ".jinja"}

def hash_file(path: Path, git_blob: bool = False) -> str:
    h = hashlib.sha1() if git_blob else hashlib.sha256()
    if git_blob:
        h.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def fast_complete(folder: Path, revision: str) -> bool:
    try:
        record = json.loads((folder / ".studio-model-complete.json").read_text(encoding="utf-8"))
        return (record.get("revision") == revision and bool(record["files"]) and
                all(wanted(x["name"]) and (folder/x["name"]).is_file() and
                    (folder/x["name"]).stat().st_size == x["size"] for x in record["files"]))
    except (OSError, ValueError, KeyError, TypeError):
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Rehash every model file; requires repository metadata if not cached")
    args = parser.parse_args()
    cfg = Config.load()
    cfg.prepare()
    folder = cfg.model_dir
    folder.mkdir(parents=True, exist_ok=True)
    if not (cfg.root/".runtime"/"license-consent.json").is_file():
        raise RuntimeError("Run.batから初回の利用条件確認を行ってください。")
    if not args.verify and fast_complete(folder, MODEL_REVISION):
        print("モデル確認済み。取得済みファイルを使用します（ネットワーク不要）。", flush=True)
        return
    from huggingface_hub import HfApi, hf_hub_download
    manifest_path = folder / ".studio-download-manifest.json"
    manifest = None
    if manifest_path.is_file():
        candidate = json.loads(manifest_path.read_text(encoding="utf-8"))
        if candidate.get("revision") == MODEL_REVISION:
            manifest = candidate
    if manifest is None:
        print("公式Hugging Faceリポジトリのファイル一覧を確認中...", flush=True)
        info = HfApi().model_info(MODEL_ID, revision=MODEL_REVISION, files_metadata=True)
        entries = []
        for sibling in info.siblings:
            if not wanted(sibling.rfilename):
                continue
            lfs = sibling.lfs
            sha = None
            if lfs:
                sha = lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)
            entries.append({"name": sibling.rfilename, "size": sibling.size,
                            "sha256": sha, "git_blob_sha1": getattr(sibling, "blob_id", None) if not sha else None})
        if not entries or any(x["size"] is None for x in entries):
            raise RuntimeError("公式ファイルサイズを取得できません。接続・アクセス権を確認してください。")
        manifest = {"repo_id": MODEL_ID, "revision": MODEL_REVISION, "files": entries}
        write_json(manifest_path, manifest)
    needed = sum(x["size"] for x in manifest["files"]
                 if not (folder/x["name"]).is_file() or (folder/x["name"]).stat().st_size != x["size"])
    free = shutil.disk_usage(folder).free
    # The runtime checks remaining files rather than blocking a resumed download using total model size.
    if free < needed + 5 * 2**30:
        raise RuntimeError(f"SSD容量不足: 残りダウンロード約{needed/2**30:.1f} GiB + 余裕5 GiB、空き{free/2**30:.1f} GiB。")
    print(f"必要なモデルファイル {len(manifest['files'])} 件。未取得約 {needed/1e9:.1f} GB。", flush=True)
    for index, entry in enumerate(manifest["files"], 1):
        name = entry["name"]
        if not wanted(name):
            raise RuntimeError("Unsafe file name in download manifest")
        path = folder / name
        print(f"[{index}/{len(manifest['files'])}] {name}", flush=True)
        valid = path.is_file() and path.stat().st_size == entry["size"]
        if valid:
            if entry.get("sha256"):
                valid = hash_file(path) == entry["sha256"]
            elif entry.get("git_blob_sha1"):
                valid = hash_file(path, git_blob=True) == entry["git_blob_sha1"]
        if not valid:
            # A corrupt final file is not useful; remove only this manifest-owned file.
            # Otherwise its replacement would temporarily require a second full shard.
            if path.is_file():
                path.unlink()
            if shutil.disk_usage(folder).free < entry["size"] + 512 * 1024**2:
                raise RuntimeError(f"SSD容量不足: {name} の再取得に約{entry['size']/2**30:.1f} GiBが必要です。")
            for attempt in range(3):
                try:
                    # Exact pinned revision. local_dir avoids a second full model copy on Windows.
                    hf_hub_download(MODEL_ID, name, revision=MODEL_REVISION,
                                    local_dir=folder, force_download=path.exists())
                    if path.stat().st_size != entry["size"]:
                        raise RuntimeError("Downloaded file length mismatch")
                    if entry.get("sha256") and hash_file(path) != entry["sha256"]:
                        raise RuntimeError("Downloaded file SHA-256 mismatch")
                    if entry.get("git_blob_sha1") and hash_file(path, git_blob=True) != entry["git_blob_sha1"]:
                        raise RuntimeError("Downloaded configuration hash mismatch")
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2 ** (attempt+1))
    model_index = json.loads((folder / "model_index.json").read_text(encoding="utf-8"))
    if model_index.get("_class_name") != "QwenImage21Pipeline":
        raise RuntimeError("モデルのパイプライン名がQwenImage21Pipelineではありません。")
    write_json(folder / ".studio-model-complete.json", manifest)
    print("公式モデルのダウンロードと整合性確認が完了しました。", flush=True)

if __name__ == "__main__":
    main()
