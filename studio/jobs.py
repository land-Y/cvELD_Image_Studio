from __future__ import annotations
import copy
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Condition, Event, Thread
from .engine import Cancelled
from .imaging import image_hash, mask_is_empty, save_png
from .schema import GenerationRequest
from .store import Store, write_json

log = logging.getLogger(__name__)

def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass
class Job:
    request: GenerationRequest
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancel: Event = field(default_factory=Event)
    state: str = "queued"
    message: str = "待機中"
    step: int = 0
    total_steps: int = 0
    image_index: int = 0
    created_at: str = field(default_factory=timestamp)
    started_at: str | None = None
    elapsed_seconds: float = 0
    started_clock: float | None = None
    seeds: list[int] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    error: str | None = None

    def public(self, result_limit: int | None = None) -> dict:
        elapsed = (time.monotonic() - self.started_clock
                   if self.started_clock is not None and self.state == "running"
                   else self.elapsed_seconds)
        return {"id": self.id, "state": self.state, "message": self.message,
                "step": self.step, "total_steps": self.total_steps, "image_index": self.image_index,
                "count": self.request.count * self.request.batch_size, "batch_count": self.request.count, "batch_size": self.request.batch_size, "created_at": self.created_at,
                "started_at": self.started_at, "elapsed_seconds": round(elapsed, 1),
                "seeds": self.seeds[:], "results": copy.deepcopy(self.results if result_limit is None else self.results[-result_limit:]),
                "error": self.error, "request": self.request.model_dump()}

class JobRunner:
    def __init__(self, engine, store: Store):
        self.engine = engine
        self.store = store
        self.cv = Condition()
        self.waiting: deque[Job] = deque()
        self.jobs: dict[str, Job] = {}
        self.active: Job | None = None
        self.closed = False
        self.worker = Thread(target=self._loop, name="studio-gpu-worker", daemon=True)
        self.worker.start()

    def submit(self, request: GenerationRequest) -> dict:
        # Verify references NOW, not after an expensive model load.
        for reference in request.references:
            self.store.upload_path(reference)
        if request.mask_id:
            mask = self.store.open_upload(request.mask_id)
            base = self.store.open_upload(request.references[0])
            if mask.size != base.size or mask_is_empty(mask):
                raise ValueError("元画像と同じ寸法で、白く塗った領域があるマスクを指定してください。")
        with self.cv:
            if self.closed:
                raise RuntimeError("終了処理中です。")
            if len(self.waiting) >= 20:
                raise ValueError("キューは最大20件です。終了後に追加してください。")
            job = Job(request=request, seeds=request.seeds())
            self.jobs[job.id] = job
            self.waiting.append(job)
            # Bound in-memory history, never prune pending or running jobs.
            for key in list(self.jobs):
                if len(self.jobs) <= 200:
                    break
                if self.jobs[key].state in ("completed", "failed", "cancelled"):
                    del self.jobs[key]
            self.cv.notify()
            return job.public()

    def list(self, compact: bool = False) -> list[dict]:
        with self.cv:
            jobs = list(reversed(list(self.jobs.values())))
            if not compact:
                return [job.public() for job in jobs]
            # Keep every pending job visible to cancellation and queue counts.
            finished = 0
            rows = []
            for job in jobs:
                if job.state not in ("queued", "running"):
                    finished += 1
                    if finished > 8:
                        continue
                rows.append(job.public(result_limit=16))
            return rows

    def get(self, job_id: str) -> dict:
        with self.cv:
            if job_id not in self.jobs:
                raise KeyError("ジョブが見つかりません。")
            return self.jobs[job_id].public()

    def cancel(self, job_id: str) -> dict:
        with self.cv:
            if job_id not in self.jobs:
                raise KeyError("ジョブが見つかりません。")
            job = self.jobs[job_id]
            if job.state in ("running", "queued"):
                job.cancel.set()
                job.message = "停止要求を受信。実行中の処理の区切りで停止します。"
                if job.state == "queued":
                    self.waiting.remove(job)
                    job.state = "cancelled"
                    job.message = "待機ジョブを取り消しました。"
            return job.public()

    def unload(self) -> None:
        with self.cv:
            if self.active or self.waiting:
                raise ValueError("実行中・待機中のジョブを終了または取り消してから解放してください。")
            self.engine.unload()

    def close(self) -> None:
        with self.cv:
            self.closed = True
            for job in self.waiting:
                job.cancel.set()
                job.state = "cancelled"
            self.waiting.clear()
            if self.active:
                self.active.cancel.set()
            self.cv.notify_all()
        self.worker.join(timeout=5)

    def _loop(self) -> None:
        while True:
            with self.cv:
                self.cv.wait_for(lambda: self.waiting or self.closed)
                if self.closed:
                    return
                job = self.waiting.popleft()
                self.active = job
                job.state = "running"
                job.started_at = timestamp()
                job.started_clock = time.monotonic()
            failed = False
            def progress(message: str, step: int, total: int) -> None:
                with self.cv:
                    job.message, job.step, job.total_steps = message, step, total
            try:
                input_hashes = {i: image_hash(self.store.upload_path(i)) for i in job.request.references}
                if job.request.mask_id:
                    input_hashes[job.request.mask_id] = image_hash(self.store.upload_path(job.request.mask_id))
                for offset in range(0, len(job.seeds), job.request.batch_size):
                    if job.cancel.is_set():
                        raise Cancelled()
                    seeds = job.seeds[offset:offset + job.request.batch_size]
                    with self.cv:
                        job.image_index = offset + 1
                        job.step = 0
                    results = self.engine.generate_batch(job.request, seeds, job.cancel, progress)
                    if len(results) != len(seeds):
                        raise RuntimeError("モデルの出力枚数が要求した同時枚数と一致しません。")
                    progress("PNGと設定を保存中", job.request.steps, job.request.steps)
                    for batch_index, (seed, result) in enumerate(zip(seeds, results)):
                        if job.cancel.is_set():
                            raise Cancelled()
                        index = offset + batch_index + 1
                        image, generated, info = result
                        filename = f"{job.id}_{index:02}.png"
                        row = {"file": filename, "url": f"/api/outputs/{filename}",
                               "settings_url": f"/api/outputs/{job.id}_{index:02}.json",
                               "job_id": job.id, "seed": seed, "created_at": timestamp(),
                               "request": job.request.model_dump(), "input_sha256": input_hashes, **info}
                        if generated is not None:
                            raw_name = f"{job.id}_{index:02}_generated.png"
                            save_png(generated, self.store.output_path(raw_name, False, mode=job.request.mode), row)
                            row["generated_url"] = f"/api/outputs/{raw_name}"
                        save_png(image, self.store.output_path(filename, False, mode=job.request.mode), row)
                        write_json(self.store.output_path(f"{job.id}_{index:02}.json", False, mode=job.request.mode), row)
                        with self.cv:
                            job.results.append(row)
                        with self.cv:
                            job.image_index = index
                with self.cv:
                    job.state, job.message = "completed", "完了"
            except Cancelled:
                failed = True
                with self.cv:
                    job.state, job.message = "cancelled", "停止しました。保存済みの画像は残っています。"
            except Exception as exc:
                failed = True
                log.exception("Job %s failed", job.id)
                text = str(exc)
                if "out of memory" in text.lower() or type(exc).__name__ == "OutOfMemoryError":
                    text = ("GPUメモリが不足しました。同時枚数を1に下げ、省VRAMモード、参照解像度512、少ない参照枚数を試してください。"
                            "解像度や画像は自動変更していません。詳細はlogs/studio.logを確認してください。")
                with self.cv:
                    job.state, job.message, job.error = "failed", "エラー", text[:2400]
            finally:
                # Exception traceback is gone here. Release GPU state on error/cancel rather
                # than retaining half-installed hooks or a partially populated inference cache.
                if failed:
                    try:
                        self.engine.unload()
                    except Exception:
                        log.exception("Recovery unload failed")
                with self.cv:
                    job.elapsed_seconds = time.monotonic() - job.started_clock
                    self.active = None
