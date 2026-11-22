"""Opt-in Android companion: paired reads and a durable photo inbox only."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import io
import json
import os
import stat
import secrets
import socket
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import FileResponse, JSONResponse, Response
import uvicorn

from .asset_security import (MAX_SINGLE_IMAGE_BYTES, MAX_NORMALIZED_IMAGE_BYTES, MAX_IMAGE_PIXELS, InvalidImageError,
    UploadTooLargeError, normalize_raster_image, read_stream_limited,
    write_private_text_atomic, harden_private_path)
from .paths import STATIC_DIR, MOBILE_INBOX_DIR
from .storage_access import check_directory_access


PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"))

# Slow 2.4 GHz Wi-Fi still needs headroom for a single phone photo upload.
UPLOAD_BODY_TIMEOUT_SECONDS = 120
THUMBNAIL_MAX_EDGE = 360


def private_ipv4(value):
    try:
        address = ipaddress.ip_address(value)
        return any(address in network for network in PRIVATE_NETWORKS)
    except ValueError:
        return False


def local_addresses():
    addresses = set()
    preferred = ""
    try:
        addresses.update(row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    # UDP connect selects a local route without sending a packet or contacting a service.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.1.1", 9))
            preferred = probe.getsockname()[0]
            addresses.add(preferred)
    except OSError:
        pass
    return sorted((value for value in addresses if private_ipv4(value) and not value.startswith("127.")),
                  key=lambda value: (value != preferred, ipaddress.IPv4Address(value)))


class MobileLink:
    def __init__(self, read_library, inbox=None, clock=time.monotonic):
        self.read_library = read_library
        self.inbox = Path(inbox) if inbox is not None else MOBILE_INBOX_DIR
        self.clock = clock
        self.lock = threading.RLock()
        self.upload_slots = threading.BoundedSemaphore(2)
        self.server = self.thread = None
        self.port = 0
        self.code = ""
        self.code_until = 0
        self.qr_ticket = self.qr_revision = ""
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
                    "qr_revision": self.qr_revision if running and self.qr_ticket and self.code_until > now else "",
                    "devices": len(self.sessions), "photos": self.list_photos()}

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise HTTPException(409, "互联服务正在运行或停止，请稍后刷新。")
            self._check_root()
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
            self.qr_ticket = self.qr_revision = ""
            if server:
                server.should_exit = True
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
        # Pending photos remain private on disk across disconnects and restarts.

    def rotate_code(self):
        with self.lock:
            self.code = f"{secrets.randbelow(100_000_000):08d}"
            self.code_until = self.clock() + 600
            self.qr_ticket = secrets.token_urlsafe(24)
            self.qr_revision = secrets.token_hex(8)
            # Rotating a code must not reset the global brute-force budget.
            return {"pairing_code": self.code, "code_seconds": 600}

    def qr_png(self, address):
        import qrcode
        with self.lock:
            if not (self.server and self.server.started and self.thread.is_alive()):
                raise HTTPException(409, "请先开启局域网互联。")
            if address not in local_addresses():
                raise HTTPException(400, "请选择当前电脑显示的局域网地址。")
            if not self.qr_ticket or self.clock() >= self.code_until:
                raise HTTPException(410, "二维码已使用或过期，请更换二维码。")
            # The fragment never goes to the HTTP server or request/referrer logs.
            url = f"http://{address}:{self.port}/#pair={self.qr_ticket}"
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        output = io.BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(output, format="PNG")
        return output.getvalue()

    def pair(self, code=None, *, ticket=None):
        with self.lock:
            now = self.clock()
            self.failures = [stamp for stamp in self.failures if stamp > now - 60]
            if len(self.failures) >= 5:
                raise HTTPException(429, "配对尝试过多，请一分钟后重试。")
            using_qr = ticket is not None
            if using_qr:
                valid = (code is None and isinstance(ticket, str) and len(ticket) == 32
                         and ticket.isascii() and bool(self.qr_ticket)
                         and hmac.compare_digest(ticket, self.qr_ticket))
            else:
                valid = (isinstance(code, str) and len(code) == 8 and code.isascii() and code.isdecimal()
                         and bool(self.code) and hmac.compare_digest(code, self.code))
            if not valid or now >= self.code_until:
                self.failures.append(now)
                raise HTTPException(403, "二维码已使用或过期，请在电脑更换二维码或手动配对。" if using_qr
                                    else "配对码错误或已过期，请查看电脑端。")
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            if len(self.sessions) >= 10:
                raise HTTPException(409, "已连接十台设备，请在电脑端关闭并重新开启互联。")
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + 12 * 3600
            if using_qr:
                # One scan consumes one ticket.  Reissue immediately so the open
                # desktop panel shows a fresh QR without resetting the manual code.
                self.qr_ticket = secrets.token_urlsafe(24)
                self.qr_revision = secrets.token_hex(8)
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

    def _check_root(self):
        if self.inbox.is_symlink():
            raise HTTPException(409, "手机收件箱目录不能是符号链接。")

    def list_photos(self):
        self._check_root()
        if not self.inbox.exists():
            return []
        rows = []
        for path in sorted(self.inbox.glob("*.json")):
            try:
                self._id(path.stem)
                if path.is_symlink() or path.stat().st_size > 4096:
                    raise ValueError()
                row = json.loads(path.read_text(encoding="utf-8"))
                if (not isinstance(row, dict) or row.get("id") != path.stem
                        or row.get("extension") not in (".jpg", ".png", ".webp", ".gif")
                        or row.get("media_type") not in ("image/jpeg", "image/png", "image/webp", "image/gif")
                        or any(type(row.get(key)) is not int or row[key] <= 0
                               for key in ("size", "width", "height", "created_at"))
                        or row["size"] > MAX_NORMALIZED_IMAGE_BYTES
                        or row["width"] * row["height"] > MAX_IMAGE_PIXELS
                        or any(not isinstance(row.get(key), str) or len(row[key]) != 64
                               or any(char not in "0123456789abcdef" for char in row[key])
                               for key in ("sha256", "normalized_sha256"))):
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

    def thumbnail(self, identity, edge=THUMBNAIL_MAX_EDGE):
        from PIL import Image
        row, data = self.photo_bytes(identity)
        edge = max(64, min(int(edge), THUMBNAIL_MAX_EDGE))
        with Image.open(io.BytesIO(data)) as source:
            source.draft("RGB", (edge, edge))
            if source.mode in ("RGBA", "LA") or (source.mode == "P" and "transparency" in source.info):
                picture = source.convert("RGBA")
                background = Image.new("RGB", picture.size, "white")
                background.paste(picture, mask=picture.getchannel("A"))
                picture = background
            else:
                picture = source.convert("RGB")
            picture.thumbnail((edge, edge))
            output = io.BytesIO()
            picture.save(output, format="JPEG", quality=80)
        return output.getvalue()

    def photo_bytes(self, identity):
        row, path = self.photo(identity)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise HTTPException(409, "照片文件类型异常。")
            data = handle.read(row["size"] + 1)
        if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["normalized_sha256"]:
            raise HTTPException(409, "照片校验失败，请保留文件并检查。")
        return row, data

    def accept(self, identity, raw):
        identity = self._id(identity)
        digest = hashlib.sha256(raw).hexdigest()
        with self.lock:
            rows = self.list_photos()
            previous = next((row for row in rows if row["id"] == identity), None)
            if previous:
                if previous["sha256"] != digest:
                    raise HTTPException(409, "同一传送编号的照片已变化，请重新选择照片。")
                self.photo_bytes(identity)
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
                   "normalized_sha256": hashlib.sha256(image.data).hexdigest(),
                   "media_type": image.media_type, "size": len(image.data),
                   "width": image.width, "height": image.height, "created_at": int(time.time())}
            if path.exists() or path.is_symlink():
                # Recover a crash after image fsync but before its receipt was committed.
                if path.is_symlink() or path.stat().st_size != len(image.data) or path.read_bytes() != image.data:
                    raise HTTPException(409, "未完成传送的照片文件存在冲突，请保留文件并检查。")
            else:
                with path.open("xb") as handle:
                    harden_private_path(path)
                    handle.write(image.data)
                    handle.flush()
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

        @router.get("/qr")
        def qr(address: str = Query(..., max_length=15)):
            return Response(self.qr_png(address), media_type="image/png", headers={
                "Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"})

        @router.get("/photos/{identity}")
        def download(identity: str):
            row, data = self.photo_bytes(identity)
            return Response(data, media_type=row["media_type"], headers={"Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="phone-{identity}{row["extension"]}"'})

        @router.get("/photos/{identity}/thumb")
        def thumbnail(identity: str):
            return Response(self.thumbnail(identity), media_type="image/jpeg",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

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
                    await asyncio.wait_for(bounded_body(), timeout=UPLOAD_BODY_TIMEOUT_SECONDS)
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

        @app.exception_handler(OSError)
        async def storage_error(_request, _error):
            return JSONResponse({"detail": "收件箱读写失败，请在电脑检查空间、权限或占用；照片未确认传送时请保留原图。"}, 503)

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
                if not isinstance(payload, dict):
                    raise HTTPException(400, "配对请求格式无效。")
                return self.pair(payload.get("code"), ticket=payload.get("ticket"))
            except (ValueError, UnicodeError):
                raise HTTPException(400, "配对请求格式无效。")

        @app.post("/api/disconnect")
        def disconnect(request: Request):
            digest = self.authorize(request)
            with self.lock:
                self.sessions.pop(digest, None)
            return {"disconnected": True}

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
