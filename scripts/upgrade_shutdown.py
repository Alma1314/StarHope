"""Ask the StarHope service that owns this folder to shut down before upgrade."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY = "Alma1314/StarHope"


def _open(url, timeout, *, headers=None, method="GET", data=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url, data=data, headers=headers or {}, method=method
    )
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def expected_instance(root):
    canonical = os.path.normcase(str(Path(root).resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lock_is_free(root):
    lock = Path(root) / ".system_generated" / "runtime.lock"
    if not lock.exists():
        return True
    try:
        with lock.open("a+b") as handle:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return True
            except OSError:
                return False
    except OSError:
        return False


def candidate_ports(root):
    try:
        row = json.loads(
            (Path(root) / ".system_generated" / "launcher-settings.json").read_text(
                encoding="utf-8-sig"
            )
        )
        port = row.get("port") if isinstance(row, dict) else None
        if type(port) is int and 1 <= port <= 65535:
            return [port]
    except (OSError, ValueError):
        pass
    return [8000]


def stop_service(root):
    instance = expected_instance(root)
    for port in candidate_ports(root):
        try:
            version = _open(f"http://127.0.0.1:{port}/api/version", 0.7)
        except (OSError, ValueError):
            continue
        if (
            not isinstance(version, dict)
            or version.get("repo") != REPOSITORY
            or version.get("project_instance_id") != instance
        ):
            continue
        token = ""
        try:
            token = (Path(root) / ".system_generated" / "local_token").read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            pass
        headers = {"X-StarHope-Launch-ID": str(version.get("server_instance_id", ""))}
        if token:
            headers["X-Local-Token"] = token
        try:
            _open(
                f"http://127.0.0.1:{port}/api/shutdown",
                5,
                headers=headers,
                method="POST",
                data=b"",
            )
        except (OSError, ValueError) as exc:
            print(
                f"[E_UPGRADE] Could not stop the running service: {exc}",
                file=sys.stderr,
            )
            return 1
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                _open(f"http://127.0.0.1:{port}/api/version", 0.4)
            except (OSError, ValueError):
                if lock_is_free(root):
                    return 0
            time.sleep(0.25)
        print("[E_UPGRADE] The service did not stop in time.", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    root = Path(arguments[0]) if arguments else Path(__file__).resolve().parents[1]
    return stop_service(root)


if __name__ == "__main__":
    raise SystemExit(main())
