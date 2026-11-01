"""User-picked, bounded browser transfers. Never execute or overwrite a download."""

from __future__ import annotations

import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

LIMIT = 6 * 1024 * 1024
EXTENSIONS = {".txt", ".csv", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx", ".xlsx", ".pptx"}


def selected_path(raw: str, *, upload: bool) -> Path:
    path = Path(raw)
    if not path.is_absolute() or raw.startswith(("\\\\", "//")) or ":" in raw[2:]:
        raise ValueError("Choose a local file using the file picker.")
    path = path.resolve(strict=upload)
    if str(path).startswith(("\\\\", "//")) or path.suffix.lower() not in EXTENSIONS:
        raise ValueError("Choose a supported document or image, up to 6 MB.")
    if upload and (not path.is_file() or path.stat().st_size > LIMIT):
        raise ValueError("Choose a file up to 6 MB.")
    if not upload and (path.exists() or not path.parent.is_dir()):
        raise ValueError(
            "Choose a new filename in an existing folder. Downloads never overwrite files."
        )
    return path


def upload_payload(raw: str) -> dict:
    path = selected_path(raw, upload=True)
    with path.open("rb") as source:
        data = source.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("File exceeds 6 MB.")
    return {"name": path.name, "mimeType": "application/octet-stream", "buffer": data}


def download(url: str, raw: str, destinations: set[str]) -> int:
    path = selected_path(raw, upload=False)

    def check(address):
        parsed = urlsplit(address)
        site = f"{parsed.scheme}://{parsed.netloc.lower()}"
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or site not in destinations
        ):
            raise ValueError("Download left the approved destinations.")

    class Redirects(HTTPRedirectHandler):
        max_redirections = 3
        max_repeats = 1

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            check(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    check(url)
    # Explicit public GET only: no cookie/credential export, environment proxy,
    # page-provided headers, browser downloads or automatic file opening.
    opener = build_opener(ProxyHandler({}), Redirects())
    deadline = time.monotonic() + 20
    written = 0
    temporary = None
    try:
        with opener.open(
            Request(url, headers={"User-Agent": "Collie local download"}), timeout=10
        ) as response:
            if int(response.headers.get("Content-Length", "0")) > LIMIT:
                raise ValueError("Download exceeds 6 MB.")
            with NamedTemporaryFile(
                mode="wb",
                prefix=".collie-download-",
                suffix=".partial",
                dir=path.parent,
                delete=False,
            ) as target:
                temporary = Path(target.name)
                while True:
                    if time.monotonic() > deadline:
                        raise ValueError("Download timed out; no automatic retry.")
                    chunk = response.read(min(65536, LIMIT + 1 - written))
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > LIMIT:
                        raise ValueError("Download exceeds 6 MB.")
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        # Commit only complete, bounded downloads. Hard-link creation fails if
        # the chosen destination appeared meanwhile, preserving that file.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return written
