from __future__ import annotations
import gc
import inspect
import logging
import os
import time
from collections import OrderedDict
from threading import Event
from typing import Callable
from PIL import Image
from .config import Config, MODEL_ID, MODEL_REVISION, DIFFUSERS_REVISION
from .imaging import mask_is_empty, preserve_outside
from .schema import GenerationRequest
from .store import Store

log = logging.getLogger(__name__)
Progress = Callable[[str, int, int], None]

class Cancelled(Exception):
    """Cooperative cancellation, only raised at safe Python boundaries."""


def effective_prompt(request: GenerationRequest) -> str:
    prompt = request.prompt
    if request.mode == "local":
        prompt = ("Edit image 1. Image 2 is a black-and-white region guide aligned with image 1. "
                  "Apply the following change inside the white region, not the black region. "
                  "Return the edited scene, without displaying the region guide. " + prompt)
    if request.transparent:
        prompt = ("Generate an RGBA image with a genuinely transparent background and an alpha channel. "
                  + prompt + " Keep the background transparent, not a checkerboard pattern.")
    return prompt


def pipeline_arguments(request: GenerationRequest, seed: int, prompt: str,
                       images: list[Image.Image], generator, callback) -> dict:
    # Names checked against the pinned upstream QwenImage21Pipeline signature.
    kwargs = {"prompt": prompt, "width": request.width, "height": request.height,
              "num_inference_steps": request.steps, "true_cfg_scale": request.cfg,
              "generator": generator, "num_images_per_prompt": request.batch_size,
              "use_kv_cache": request.use_kv_cache,
              "output_resolution": request.reference_resolution,
              "output_type": "pil", "callback_on_step_end": callback,
              "callback_on_step_end_tensor_inputs": ["latents"]}
    if images:
        kwargs["image"] = images
    if request.cfg > 1.0:
        kwargs["negative_prompt"] = request.negative_prompt or " "
    return kwargs

def make_scheduler(config, sampler: str):
    """Rebuild from model defaults, never inherit the previous job's schedule."""
    from diffusers import FlowMatchEulerDiscreteScheduler
    if sampler not in ("euler", "euler_karras", "euler_exponential"):
        raise ValueError("未対応のサンプリング方式です。")
    return FlowMatchEulerDiscreteScheduler.from_config(
        config, use_karras_sigmas=sampler == "euler_karras",
        use_exponential_sigmas=sampler == "euler_exponential",
        use_beta_sigmas=False, stochastic_sampling=False)


class Engine:
    """Single GPU worker owns this object. No remote calls occur during inference."""
    def __init__(self, config: Config, store: Store):
        self.config = config
        self.store = store
        self.pipe = None
        self.torch = None
        self.loaded_profile: str | None = None
        self.cache: OrderedDict[str, tuple] = OrderedDict()
        self.cache_bytes = 0
        self.load_seconds = 0.0
        self.scheduler_config = None

    def state(self) -> dict:
        return {"loaded": self.pipe is not None, "profile": self.loaded_profile,
                "text_cache_entries": len(self.cache),
                "text_cache_mib": round(self.cache_bytes / 2**20, 1),
                "last_load_seconds": round(self.load_seconds, 2)}

    def unload(self) -> None:
        p, self.pipe = self.pipe, None
        self.scheduler_config = None
        self.loaded_profile = None
        self.cache.clear()
        self.cache_bytes = 0
        if p is not None:
            try:
                p.maybe_free_model_hooks()
            except Exception:
                log.exception("Could not free all model hooks; releasing references")
            del p
        gc.collect()
        if self.torch is not None and self.torch.cuda.is_available():
            with self.torch.cuda.device(self.config.gpu_index):
                self.torch.cuda.empty_cache()

    def _load(self, profile: str, progress: Progress, cancel: Event) -> None:
        if self.pipe is not None and self.loaded_profile == profile:
            return
        self.unload()
        if cancel.is_set():
            raise Cancelled()
        if not (self.config.model_dir / "model_index.json").is_file():
            raise RuntimeError("モデルが未取得です。Run.batまたはRepair.batを実行してください。")
        progress("モデルを読み込み中（初回・モード変更時）", 0, 0)
        import torch
        # Avoid competing mapped-file reads on slower disks. An explicit user override wins.
        os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")
        from diffusers import QwenImage21Pipeline
        from diffusers.models.transformers.transformer_qwenimage21 import QwenImage21AttnProcessor
        self.torch = torch
        if not torch.cuda.is_available():
            raise RuntimeError("CUDAが使用できません。Diagnose.batでドライバーとPyTorchを確認してください。")
        torch.cuda.set_device(self.config.gpu_index)
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("BF16対応GPUが必要です。この構成はRTX 5090向けです。")
        torch.set_float32_matmul_precision("high")
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        start = time.perf_counter()
        pipe = QwenImage21Pipeline.from_pretrained(
            str(self.config.model_dir), torch_dtype=torch.bfloat16,
            use_safetensors=True, local_files_only=True, low_cpu_mem_usage=True)
        # Exact segmented block-causal attention. Never enable uncompiled flex_attention.
        pipe.transformer.set_attn_processor(QwenImage21AttnProcessor())
        if hasattr(pipe.text_encoder, "set_attn_implementation"):
            pipe.text_encoder.set_attn_implementation("sdpa")
        if hasattr(pipe.vae, "enable_tiling"):
            pipe.vae.enable_tiling()
        if profile == "low_memory":
            pipe.enable_sequential_cpu_offload(gpu_id=self.config.gpu_index)
        else:
            pipe.enable_model_cpu_offload(gpu_id=self.config.gpu_index)
        pipe.set_progress_bar_config(disable=True)
        required = {"image", "output_resolution", "use_kv_cache", "callback_on_step_end"}
        if not required.issubset(inspect.signature(pipe.__call__).parameters):
            raise RuntimeError("Diffusers APIが固定バージョンと一致しません。Repair.batで修復してください。")
        self.scheduler_config = dict(pipe.scheduler.config)
        self.pipe = pipe
        self.loaded_profile = profile
        self.load_seconds = time.perf_counter() - start
        if cancel.is_set():
            raise Cancelled()

    def _text_embeddings(self, text: str):
        cached = self.cache.get(text)
        if cached is not None:
            self.cache.move_to_end(text)
            return tuple(x.to(f"cuda:{self.config.gpu_index}") if x is not None else None
                         for x in cached), True
        # T2I only: image-conditioned embeddings need a public image_pad_mask argument,
        # which the pinned __call__ does not expose. Never cache image-conditioned prompts.
        embeds, mask, _ = self.pipe.encode_prompt(text, image=None,
                                                 device=f"cuda:{self.config.gpu_index}")
        cpu = (embeds.detach().to("cpu"), mask.detach().to("cpu") if mask is not None else None)
        size = sum(x.numel() * x.element_size() for x in cpu if x is not None)
        budget = self.config.text_cache_mb * 2**20
        while self.cache and self.cache_bytes + size > budget:
            _, removed = self.cache.popitem(last=False)
            self.cache_bytes -= sum(x.numel() * x.element_size() for x in removed if x is not None)
        if size <= budget:
            self.cache[text] = cpu
            self.cache_bytes += size
        return (embeds, mask), False

    def generate(self, request: GenerationRequest, seed: int, cancel: Event,
                 progress: Progress) -> tuple[Image.Image, Image.Image | None, dict]:
        return self.generate_batch(request.model_copy(update={"batch_size": 1}), [seed], cancel, progress)[0]

    def generate_batch(self, request: GenerationRequest, seeds: list[int], cancel: Event,
                       progress: Progress) -> list[tuple[Image.Image, Image.Image | None, dict]]:
        if len(seeds) != request.batch_size:
            raise ValueError("Seed数が同時枚数と一致しません。")
        self._load(request.profile, progress, cancel)
        self.pipe.scheduler = make_scheduler(self.scheduler_config, request.sampler)
        torch = self.torch
        images = [self.store.open_upload(i).convert("RGBA") for i in request.references]
        original = images[0] if images else None
        mask = None
        if request.mode == "local":
            mask = self.store.open_upload(request.mask_id).convert("L")
            if mask.size != original.size:
                raise ValueError("部分編集の元画像とマスクの寸法が一致しません。マスクを描き直してください。")
            if mask_is_empty(mask):
                raise ValueError("マスクが空です。編集したい部分を白く塗ってください。")
            images.insert(1, mask.convert("RGBA"))
        prompt = effective_prompt(request)
        generators = [torch.Generator(device=f"cuda:{self.config.gpu_index}").manual_seed(seed) for seed in seeds]
        generator = generators[0] if len(generators) == 1 else generators
        def callback(pipe, step_index: int, timestep, callback_kwargs: dict):
            if cancel.is_set():
                raise Cancelled()
            progress("画像を生成中", step_index + 1, request.steps)
            return callback_kwargs
        kwargs = pipeline_arguments(request, seeds[0], prompt, images, generator, callback)
        cache_hit = False
        start = time.perf_counter()
        with torch.inference_mode():
            torch.cuda.reset_peak_memory_stats(self.config.gpu_index)
            if request.mode == "generate" and request.cache_text and self.config.text_cache_mb:
                progress("文章を読み込み中／テキストキャッシュ確認", 0, 0)
                (embeds, mask_embeds), cache_hit = self._text_embeddings(prompt)
                kwargs.pop("prompt")
                kwargs["prompt_embeds"] = embeds
                kwargs["prompt_embeds_mask"] = mask_embeds
                if request.cfg > 1:
                    (negative, negative_mask), _ = self._text_embeddings(request.negative_prompt or " ")
                    kwargs.pop("negative_prompt", None)
                    kwargs["negative_prompt_embeds"] = negative
                    kwargs["negative_prompt_embeds_mask"] = negative_mask
            if cancel.is_set():
                raise Cancelled()
            progress("前処理・参照画像の読み込み", 0, request.steps)
            output_images = self.pipe(**kwargs).images
            if len(output_images) != len(seeds):
                raise RuntimeError("モデルの出力枚数が要求した同時枚数と一致しません。")
            if cancel.is_set():
                raise Cancelled()
            torch.cuda.synchronize(self.config.gpu_index)
            peak = torch.cuda.max_memory_allocated(self.config.gpu_index) / 2**30
        info = {"effective_prompt": prompt, "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
                "diffusers_revision": DIFFUSERS_REVISION,
                "torch": torch.__version__, "cuda_runtime": torch.version.cuda,
                "gpu": torch.cuda.get_device_name(self.config.gpu_index),
                "inference_seconds": round(time.perf_counter() - start, 3),
                "peak_allocated_gib": round(peak, 3), "text_cache_hit": cache_hit,
                "attention": "exact segmented SDPA", "dtype": "bfloat16",
                "quantization": "none", "sampler": request.sampler,
                "batch_size": len(seeds), "timing_scope": "batch"}
        results = []
        for image in output_images:
            generated = None
            if request.mode == "local" and request.preserve_outside:
                generated = image
                image = preserve_outside(original, generated, mask, request.mask_feather)
            image_info = {**info, "final_width": image.width, "final_height": image.height,
                "has_alpha_channel": image.mode == "RGBA",
                "has_transparent_pixels": image.mode == "RGBA" and image.getchannel("A").getextrema()[0] < 255,
                "composited_on_original_grid": generated is not None}
            results.append((image, generated, image_info))
        return results
