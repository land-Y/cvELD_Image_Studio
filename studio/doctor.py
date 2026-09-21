from __future__ import annotations
import importlib.metadata
import inspect
import json
import platform
import sys
import subprocess
import traceback
from datetime import datetime, timezone
import psutil
from .config import Config
from .store import write_json


def diagnose() -> tuple[dict, bool]:
    cfg = Config.load()
    cfg.prepare()
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "os": platform.platform(),
              "python": sys.version, "ram_gib": round(psutil.virtual_memory().total / 2**30, 2),
              "packages": {}, "gpu": {}, "warnings": [], "errors": []}
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=15, check=False)
        report["nvidia_smi"] = result.stdout.strip() or result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        report["nvidia_smi"] = "not available; CUDA kernel test remains authoritative"
    try:
        for name in ("torch", "torchvision", "transformers", "diffusers", "accelerate", "huggingface-hub",
                     "fastapi", "Pillow", "safetensors"):
            report["packages"][name] = importlib.metadata.version(name)
        import torch
        import torchvision
        from transformers import Qwen3VLForConditionalGeneration, Qwen3VLProcessor
        from diffusers import QwenImage21Pipeline
        from diffusers.models.transformers.transformer_qwenimage21 import QwenImage21AttnProcessor
        assert callable(QwenImage21AttnProcessor)
        report["pipeline_parameters"] = list(inspect.signature(QwenImage21Pipeline.__call__).parameters)
        for key in ("image", "use_kv_cache", "output_resolution", "callback_on_step_end", "prompt_embeds"):
            if key not in report["pipeline_parameters"]:
                raise RuntimeError(f"Missing pipeline parameter: {key}")
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable. Install a current NVIDIA driver and retry; do not install CPU-only torch.")
        if torch.cuda.device_count() <= cfg.gpu_index:
            raise RuntimeError("config.json gpu_index does not identify an available GPU.")
        torch.cuda.set_device(cfg.gpu_index)
        properties = torch.cuda.get_device_properties(cfg.gpu_index)
        capability = torch.cuda.get_device_capability(cfg.gpu_index)
        report["gpu"] = {"name": properties.name, "vram_gib": round(properties.total_memory/2**30, 2),
                         "compute_capability": list(capability), "cuda_runtime": torch.version.cuda,
                         "compiled_arches": torch.cuda.get_arch_list(), "index": cfg.gpu_index}
        if "5090" not in properties.name:
            report["warnings"].append("GPU is not RTX 5090. This package is tuned for a single 5090; other GPUs are unvalidated.")
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("The selected GPU does not support BF16.")
        # Capability strings alone are insufficient: actually execute BF16 GEMM + SDPA.
        with torch.inference_mode():
            a = torch.randn(128, 128, device="cuda", dtype=torch.bfloat16)
            b = a @ a.T
            q = torch.randn(1, 4, 64, 64, device="cuda", dtype=torch.bfloat16)
            c = torch.nn.functional.scaled_dot_product_attention(q, q, q)
            torch.cuda.synchronize()
            if not torch.isfinite(b).all().item() or not torch.isfinite(c).all().item():
                raise RuntimeError("CUDA kernel test produced non-finite values.")
            del a, b, q, c
        torch.cuda.empty_cache()
        report["cuda_kernel_test"] = "passed"
        if report["ram_gib"] < 60:
            report["warnings"].append("64 GB or more physical RAM is recommended. Large models may page to disk with less RAM.")
    except Exception as exc:
        report["errors"].append(str(exc))
        report["traceback"] = traceback.format_exc()
        report["cuda_kernel_test"] = "not passed"
    report["full_model_generation_test"] = "NOT performed by this diagnostic; use a 512px job in the UI."
    write_json(cfg.root / ".runtime" / "hardware.json", report)
    return report, not report["errors"]


def main():
    report, success = diagnose()
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not success:
        print("\n診断に失敗しました。モデルはまだダウンロードしません。logsとマニュアルを確認してください。", flush=True)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
