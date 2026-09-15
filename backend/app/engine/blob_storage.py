"""
Thin client for Vercel Blob (https://vercel.com/docs/storage/vercel-blob),
used in place of the local filesystem for uploaded document originals -
Vercel's Python serverless functions have no persistent/writable disk
outside /tmp, so nothing can be written to a path like
storage/{user_id}/{document_id}/original.<ext> and expected to survive
past the current invocation, let alone be readable by a later request.

There is no official Vercel Blob SDK for Python (only JS/TS), so this
module talks to its plain REST API directly via httpx:
  - PUT  https://blob.vercel-storage.com/{pathname}  -> upload
  - DELETE https://blob.vercel-storage.com           -> delete (by URL, in
    the request body, not the path)
Every request is authenticated with `Authorization: Bearer
{BLOB_READ_WRITE_TOKEN}` - the fixed env var name Vercel injects into the
project automatically once Blob storage is provisioned for it (see the
README's "Environment variables" section). Configuration is read directly
via os.getenv, matching the rest of app/engine/'s isolation contract (see
app/engine/__init__.py) - this module has zero dependency on
app.core.config or the DB/auth stack.

Nothing here needs a local temp file: callers already have the upload's
raw bytes in memory (from FastAPI's UploadFile.read()) before calling
upload_blob(), and app/engine/extraction.py already operates on raw bytes
(io.BytesIO / pymupdf's stream= parameter) rather than a filesystem path -
so no /tmp staging step is actually required in this codebase. Use
tempfile.NamedTemporaryFile / `/tmp` only if some future processing step
genuinely needs a real file path on disk.
"""

import os
from dataclasses import dataclass
from typing import Optional

import httpx

BLOB_API_BASE = "https://blob.vercel-storage.com"
_BLOB_API_VERSION = "7"


class BlobStorageError(RuntimeError):
    """Raised when a Vercel Blob request fails or the token is missing."""


def _token() -> str:
    token = (os.getenv("BLOB_READ_WRITE_TOKEN") or "").strip()
    if not token:
        raise BlobStorageError(
            "BLOB_READ_WRITE_TOKEN is not set - provision Vercel Blob storage "
            "for this project and set the env var it generates."
        )
    return token


def _headers(extra: Optional[dict] = None) -> dict:
    headers = {
        "Authorization": f"Bearer {_token()}",
        "x-api-version": _BLOB_API_VERSION,
    }
    if extra:
        headers.update(extra)
    return headers


@dataclass(frozen=True)
class UploadedBlob:
    url: str
    pathname: str


def upload_blob(pathname: str, data: bytes, content_type: Optional[str] = None) -> UploadedBlob:
    """PUT raw bytes to Vercel Blob at `pathname`, returning its public
    URL. `add-random-suffix` is disabled since callers here already build
    a unique pathname themselves (e.g. f"{user_id}/{document_id}/original.pdf"),
    so re-uploading the same document id overwrites the same blob rather
    than accumulating new ones with random suffixes."""
    headers = _headers(
        {
            "x-content-type": content_type or "application/octet-stream",
            "x-add-random-suffix": "0",
        }
    )
    try:
        response = httpx.put(
            f"{BLOB_API_BASE}/{pathname}", content=data, headers=headers, timeout=60.0
        )
    except httpx.HTTPError as exc:
        raise BlobStorageError(f"Vercel Blob upload request failed: {exc}") from exc

    if response.status_code >= 400:
        raise BlobStorageError(
            f"Vercel Blob upload failed ({response.status_code}): {response.text}"
        )

    body = response.json()
    url = body.get("url")
    if not url:
        raise BlobStorageError("Vercel Blob upload response had no 'url' field.")
    return UploadedBlob(url=url, pathname=pathname)


def download_blob(url: str) -> bytes:
    """GET the raw bytes of a previously-uploaded blob by its public URL -
    for a processing step that needs the original file again."""
    try:
        response = httpx.get(url, timeout=60.0)
    except httpx.HTTPError as exc:
        raise BlobStorageError(f"Vercel Blob download request failed: {exc}") from exc

    if response.status_code >= 400:
        raise BlobStorageError(
            f"Vercel Blob download failed ({response.status_code}): {response.text}"
        )
    return response.content


def delete_blob(url: str) -> None:
    """Delete a blob by its public URL - Vercel Blob's delete endpoint
    takes url(s) in the JSON body, not the path."""
    try:
        response = httpx.request(
            "DELETE",
            BLOB_API_BASE,
            headers=_headers({"Content-Type": "application/json"}),
            json={"urls": [url]},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise BlobStorageError(f"Vercel Blob delete request failed: {exc}") from exc

    if response.status_code >= 400:
        raise BlobStorageError(
            f"Vercel Blob delete failed ({response.status_code}): {response.text}"
        )
