from __future__ import annotations
import json
import logging
import logging.handlers
import socket
import threading
import time
import urllib.request
import webbrowser
from .config import Config


def main() -> None:
    from .console_lifetime import install_console_lifetime
    install_console_lifetime()
    config = Config.load()
    config.prepare()
    # One launcher/server per extracted folder, with an OS-released lock, not a stale PID file.
    from .locking import FolderLock
    with FolderLock(config.root / ".runtime" / "server.lock"):
        handlers = [logging.StreamHandler(), logging.handlers.RotatingFileHandler(
            config.root / "logs" / "studio.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")]
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            handlers=handlers)
        # Bind in advance and pass the same socket to uvicorn: avoids a check-then-bind race.
        sock = None
        for port in range(config.port, min(config.port + 20, 65536)):
            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                candidate.bind(("127.0.0.1", port))
                candidate.listen(128)
                sock = candidate
                break
            except OSError:
                candidate.close()
        if sock is None:
            raise RuntimeError("使用できるポートがありません。config.jsonのportを変更してください。")
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        print(f"\ncvELD Image Studio: {url}\n終了: このウィンドウで Ctrl+C\n", flush=True)
        def open_ready() -> None:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                        if json.load(response).get("app") == "cvELD Image Studio":
                            webbrowser.open(url)
                            return
                except Exception:
                    time.sleep(.2)
        from .server import create_app
        import uvicorn
        app = create_app(config)
        from .store import write_json
        running_file = config.root / ".runtime" / "server.json"
        write_json(running_file, {"root": str(config.root.resolve()), "url": url,
                                  "instance_id": app.state.instance_id})
        if config.open_browser:
            threading.Thread(target=open_ready, daemon=True).start()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=sock.getsockname()[1],
                                              log_level="info", access_log=False, workers=1))
        try:
            server.run(sockets=[sock])
        finally:
            running_file.unlink(missing_ok=True)
            sock.close()

if __name__ == "__main__":
    main()
