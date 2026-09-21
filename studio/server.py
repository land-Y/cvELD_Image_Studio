from __future__ import annotations
import hmac
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from . import __version__
from .config import Config, MODEL_ID, MODEL_REVISION
from .engine import Engine
from .imaging import MAX_UPLOAD_BYTES
from .jobs import JobRunner
from .schema import GenerationRequest
from .store import Store

log = logging.getLogger(__name__)


def create_app(config: Config, engine_factory=Engine) -> FastAPI:
    config.prepare()
    store = Store(config.data_dir)
    engine = engine_factory(config, store)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = JobRunner(engine, store)
        yield
        app.state.runner.close()

    app = FastAPI(title="cvELD Image Studio", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.instance_id = secrets.token_hex(16)
    app.state.token = token  # Accessible to local automated tests; never printed in logs.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def protect_local_api(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD"):
            if not hmac.compare_digest(request.headers.get("x-studio-token", ""), token):
                return JSONResponse({"detail": "セッションが無効です。画面を再読み込みしてください。"}, status_code=403)
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
                    return JSONResponse({"detail": "外部サイトからの操作は拒否されました。"}, status_code=403)
            length = request.headers.get("content-length")
            if length:
                try:
                    too_big = int(length) > MAX_UPLOAD_BYTES
                except ValueError:
                    too_big = True
                if too_big:
                    return JSONResponse({"detail": "アップロード上限は32 MiBです。"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if request.url.path in ("/", "/manual") or request.url.path.startswith(("/api/", "/assets/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def translate_error(exc: Exception) -> HTTPException:
        code = 404 if isinstance(exc, (FileNotFoundError, KeyError)) else 400
        return HTTPException(code, str(exc))

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (config.root / "web" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__STUDIO_TOKEN__", token))

    @app.get("/manual", response_class=HTMLResponse)
    def manual():
        return FileResponse(config.root / "docs" / "Manual_ja.html", media_type="text/html")

    @app.get("/api/health")
    def health():
        return {"ok": True, "app": "cvELD Image Studio", "version": __version__, "instance_id": app.state.instance_id}

    @app.get("/api/status")
    def status():
        hardware_file = config.root / ".runtime" / "hardware.json"
        try:
            hardware = json.loads(hardware_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hardware = {}
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(str(config.data_dir))
        return {"version": __version__, "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
                "model_downloaded": (config.model_dir / ".studio-model-complete.json").is_file(),
                "engine": engine.state(), "hardware": hardware,
                "ram_used_gib": round(vm.used / 2**30, 1), "ram_total_gib": round(vm.total / 2**30, 1),
                "disk_free_gib": round(disk.free / 2**30, 1),
                "output_directory": str(store.outputs),
                "research_license": True}

    async def read_upload(request: Request) -> bytes:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "画像は32 MiB以下にしてください。")
        return bytes(data)

    @app.post("/api/uploads")
    async def upload(request: Request, mask: bool = False):
        data = await read_upload(request)
        try:
            return await run_in_threadpool(store.upload, data, mask=mask)
        except (ValueError, OSError) as exc:
            raise translate_error(exc) from exc

    @app.get("/api/uploads/{image_id}.png")
    def input_image(image_id: str):
        try:
            return FileResponse(store.upload_path(image_id), media_type="image/png")
        except (ValueError, OSError) as exc:
            raise translate_error(exc) from exc

    @app.get("/api/outputs/{name}")
    def output(name: str, download: bool = False):
        try:
            path = store.output_path(name)
            return FileResponse(path, filename=name if download else None,
                                media_type="image/png" if path.suffix == ".png" else "application/json")
        except (ValueError, OSError) as exc:
            raise translate_error(exc) from exc

    @app.get("/api/history")
    def history():
        return store.history()

    @app.get("/api/jobs")
    def jobs():
        return app.state.runner.list(compact=True)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        try:
            return app.state.runner.get(job_id)
        except KeyError as exc:
            raise translate_error(exc) from exc

    @app.post("/api/jobs")
    def submit(request: GenerationRequest):
        try:
            return app.state.runner.submit(request)
        except (ValueError, OSError, RuntimeError) as exc:
            raise translate_error(exc) from exc

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        try:
            return app.state.runner.cancel(job_id)
        except KeyError as exc:
            raise translate_error(exc) from exc

    @app.post("/api/unload")
    def unload():
        try:
            app.state.runner.unload()
            return {"ok": True}
        except ValueError as exc:
            raise translate_error(exc) from exc

    app.mount("/assets", StaticFiles(directory=config.root / "web"), name="assets")
    return app
