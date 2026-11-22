"""Opt-in Android companion: paired reads and a durable photo inbox only."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import FileResponse, JSONResponse, Response
import uvicorn

from .asset_security import (MAX_SINGLE_IMAGE_BYTES, InvalidImageError,
    UploadTooLargeError, normalize_raster_image, read_stream_limited,
    write_private_text_atomic, harden_private_path)
from .paths import STATIC_DIR, SYSTEM_GENERATED_DIR
from .storage_access import check_directory_access


PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"))


def private_ipv4(value):
    try:
        address = ipaddress.ip_address(value)
        return any(address in network for network in PRIVATE_NETWORKS)
    except ValueError:
        return False


def local_addresses():
    addresses = set()
    try:
        addresses.update(row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    # UDP connect selects a local route without sending a packet or contacting a service.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.1.1", 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(value for value in addresses if private_ipv4(value) and not value.startswith("127."))


class MobileLink:
    def __init__(self, read_library, inbox=None, clock=time.monotonic):
        self.read_library = read_library
        self.inbox = Path(inbox) if inbox is not None else SYSTEM_GENERATED_DIR / "mobile_inbox"
        self.clock = clock
        self.lock = threading.RLock()
        self.upload_slots = threading.BoundedSemaphore(2)
        self.server = self.thread = None
        self.port = 0
        self.code = ""
        self.code_until = 0
        self.sessions = {}
        self.failures = []
        self.mobile_app = self._mobile_app()

    def status(self):
        with self.lock:
            now = self.clock()
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            running = bool(self.server and self.server.started and self.thread.is_alive())
            return {"enabled": running, "port": self.port if running else None,
                    "urls": [f"http://{address}:{self.port}" for address in local_addresses()] if running else [],
                    "pairing_code": self.code if running and self.code_until > now else "",
                    "code_seconds": max(0, int(self.code_until - now)) if running else 0,
                    "devices": len(self.sessions), "photos": self.list_photos()}

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise HTTPException(409, "互联服务正在运行或停止，请稍后刷新。")
            check_directory_access(self.inbox, label="手机照片收件箱")
            harden_private_path(self.inbox, directory=True)
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                listener.bind(("0.0.0.0", 0))
                listener.listen(32)
                self.port = listener.getsockname()[1]
                config = uvicorn.Config(self.mobile_app, host="0.0.0.0", port=self.port,
                    access_log=False, log_level="warning", proxy_headers=False,
                    timeout_keep_alive=5, limit_concurrency=16)
                self.server = uvicorn.Server(config)
                self.thread = threading.Thread(target=self.server.run,
                    kwargs={"sockets": [listener]}, name="starhope-mobile-link", daemon=True)
                self.sessions.clear()
                self.rotate_code()
                self.thread.start()
            except Exception:
                listener.close()
                raise
        for _ in range(100):
            if self.server.started:
                return self.status()
            if not self.thread.is_alive():
                break
            time.sleep(.02)
        self.close()
        raise HTTPException(503, "无法开启局域网互联，请检查防火墙和网络。")

    def close(self):
        with self.lock:
            server, thread = self.server, self.thread
            self.sessions.clear()
            self.code = ""
            self.code_until = 0
            if server:
                server.should_exit = True
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
        # Pending photos remain private on disk across disconnects and restarts.

    def rotate_code(self):
        with self.lock:
            self.code = f"{secrets.randbelow(100_000_000):08d}"
            self.code_until = self.clock() + 600
            # Rotating a code must not reset the global brute-force budget.
            return {"pairing_code": self.code, "code_seconds": 600}

    def pair(self, code):
        with self.lock:
            now = self.clock()
            self.failures = [stamp for stamp in self.failures if stamp > now - 60]
            if len(self.failures) >= 5:
                raise HTTPException(429, "配对尝试过多，请一分钟后重试。")
            if (not isinstance(code, str) or now >= self.code_until or not self.code
                    or not hmac.compare_digest(code, self.code)):
                self.failures.append(now)
                raise HTTPException(403, "配对码错误或已过期，请查看电脑端。")
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            if len(self.sessions) >= 10:
                raise HTTPException(409, "已连接十台设备，请在电脑端关闭并重新开启互联。")
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + 12 * 3600
            return {"token": token, "expires_seconds": 12 * 3600}

    def authorize(self, request):
        token = request.headers.get("authorization", "")
        if not token.startswith("Bearer ") or len(token) > 200:
            raise HTTPException(401, "请先配对电脑。")
        digest = hashlib.sha256(token[7:].encode()).hexdigest()
        with self.lock:
            if self.sessions.get(digest, 0) <= self.clock():
                raise HTTPException(401, "连接已失效，请重新配对。")
        return digest

    def _id(self, value):
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "照片编号无效。")
        return value

    def list_photos(self):
        if not self.inbox.exists():
            return []
        rows = []
        for path in sorted(self.inbox.glob("*.json")):
            try:
                self._id(path.stem)
                if path.is_symlink():
                    raise ValueError()
                row = json.loads(path.read_text(encoding="utf-8"))
                if row["id"] != path.stem or row["extension"] not in (".jpg", ".png", ".webp", ".gif"):
                    raise ValueError()
                rows.append(row)
            except Exception:
                raise HTTPException(409, "手机照片收件箱记录异常，请保留文件并检查，未自动清理。")
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    def photo(self, identity):
        with self.lock:
            identity = self._id(identity)
            row = next((row for row in self.list_photos() if row["id"] == identity), None)
            if not row:
                raise HTTPException(404, "照片已移除。")
            path = self.inbox / (identity + row["extension"])
            if path.is_symlink() or not path.is_file():
                raise HTTPException(409, "照片文件不可用，请重新传送。")
            return row, path

    def accept(self, identity, raw):
        identity = self._id(identity)
        digest = hashlib.sha256(raw).hexdigest()
        with self.lock:
            rows = self.list_photos()
            previous = next((row for row in rows if row["id"] == identity), None)
            if previous:
                if previous["sha256"] != digest:
                    raise HTTPException(409, "同一传送编号的照片已变化，请重新选择照片。")
                self.photo(identity)
                return previous
        image = normalize_raster_image(raw)
        with self.lock:
            # Recheck after decoding: concurrent replay must never overwrite a receipt.
            rows = self.list_photos()
            if any(row["id"] == identity for row in rows):
                return self.accept(identity, raw)
            if len(rows) >= 20 or sum(row["size"] for row in rows) + len(image.data) > 128 * 1024 * 1024:
                raise HTTPException(409, "电脑收件箱已满（20 张或 128 MiB），请处理后移除照片。")
            check_directory_access(self.inbox, label="手机照片收件箱")
            path = self.inbox / (identity + image.extension)
            row = {"id": identity, "sha256": digest, "extension": image.extension,
                   "media_type": image.media_type, "size": len(image.data),
                   "width": image.width, "height": image.height, "created_at": int(time.time())}
            with path.open("xb") as handle:
                harden_private_path(path)
                handle.write(image.data)
                handle.flush()
                import os
                os.fsync(handle.fileno())
            try:
                write_private_text_atomic(self.inbox / (identity + ".json"), json.dumps(row))
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return row

    def remove(self, identity):
        with self.lock:
            row, path = self.photo(identity)
            path.unlink()
            (self.inbox / (row["id"] + ".json")).unlink()
            return {"removed": True}

    def desktop_router(self):
        router = APIRouter(prefix="/api/mobile-link")
        router.add_api_route("", self.status, methods=["GET"])
        router.add_api_route("/start", self.start, methods=["POST"])
        router.add_api_route("/stop", self.close, methods=["POST"])
        router.add_api_route("/code", self.rotate_code, methods=["POST"])

        @router.get("/photos/{identity}")
        def download(identity: str):
            row, path = self.photo(identity)
            return FileResponse(path, media_type=row["media_type"], filename="phone-" + identity + row["extension"],
                                headers={"Cache-Control": "no-store"})

        @router.delete("/photos/{identity}")
        def remove(identity: str):
            return self.remove(identity)
        return router

    def _mobile_app(self):
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.middleware("http")
        async def guard(request: Request, call_next):
            # Only literal LAN IPv4 hosts and peers; forwarded headers are never trusted.
            if (not request.client or not private_ipv4(request.client.host)
                    or not private_ipv4(request.url.hostname or "")
                    or request.url.port != self.port):
                return JSONResponse({"detail": "仅允许局域网直接连接。"}, 403)
            origin = request.headers.get("origin")
            if origin and origin != f"http://{request.headers.get('host')}":
                return JSONResponse({"detail": "不允许跨站请求。"}, 403)
            acquired = False
            try:
                if request.url.path.startswith("/api/") and request.url.path != "/api/pair":
                    self.authorize(request)
                if request.method == "POST":
                    maximum = MAX_SINGLE_IMAGE_BYTES + 65536 if request.url.path == "/api/photos" else 1024
                    length = request.headers.get("content-length", "")
                    if not length.isdigit() or int(length) > maximum:
                        return JSONResponse({"detail": "请求过大或缺少有效长度。"}, 413)
                    acquired = self.upload_slots.acquire(blocking=False)
                    if not acquired:
                        return JSONResponse({"detail": "电脑正在接收其他照片，请稍后重试。"}, 429)
                    chunks, size = [], 0
                    async def bounded_body():
                        nonlocal size
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > maximum:
                                raise HTTPException(413, "照片超过上传上限。")
                            chunks.append(chunk)
                    await asyncio.wait_for(bounded_body(), timeout=30)
                    request._body = b"".join(chunks)
                response = await call_next(request)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, exc.status_code)
            except (TimeoutError, asyncio.TimeoutError):
                response = JSONResponse({"detail": "照片接收超时，请保留照片并重试。"}, 408)
            finally:
                if acquired:
                    self.upload_slots.release()
            response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff", "Content-Security-Policy":
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
            return response

        @app.get("/")
        def page():
            return FileResponse(STATIC_DIR / "mobile.html")

        @app.get("/mobile.js")
        def script():
            return FileResponse(STATIC_DIR / "js" / "mobile.js")

        @app.get("/mobile.css")
        def style():
            return FileResponse(STATIC_DIR / "css" / "mobile.css")

        @app.post("/api/pair")
        async def pair(request: Request):
            try:
                payload = await request.json()
                return self.pair(payload.get("code") if isinstance(payload, dict) else None)
            except (ValueError, UnicodeError):
                raise HTTPException(400, "配对请求格式无效。")

        @app.get("/api/library")
        def library(q: str = Query("", max_length=200), page: int = Query(1, ge=1, le=100000)):
            return self.read_library(q, page)

        @app.get("/api/connection")
        def connection():
            return {"connected": True, "max_image_bytes": MAX_SINGLE_IMAGE_BYTES}

        @app.post("/api/photos")
        def upload(identity: str, file: UploadFile = File(...)):
            try:
                raw = read_stream_limited(file.file, MAX_SINGLE_IMAGE_BYTES)
                return self.accept(identity, raw)
            except UploadTooLargeError:
                raise HTTPException(413, "照片超过单图上传上限。")
            except InvalidImageError:
                raise HTTPException(400, "照片格式无效，请选择 JPEG、PNG 或 WebP 图片。")
            finally:
                file.file.close()
        return app
