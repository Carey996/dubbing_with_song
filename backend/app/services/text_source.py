from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from starlette.datastructures import UploadFile


def read_txt_file(path: Path) -> str:
    if not path.name.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are supported.")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=400, detail=f"Txt file not found: {path}")
    return decode_txt_content(path.read_bytes(), empty_detail="Txt file is empty.")


async def read_txt_upload(upload: UploadFile) -> str:
    filename = Path(upload.filename or "source.txt").name
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are supported for text upload.")

    raw = await upload.read()
    return decode_txt_content(raw, empty_detail="Uploaded txt file is empty.")


def decode_txt_content(raw: bytes, empty_detail: str) -> str:
    if not raw:
        raise HTTPException(status_code=400, detail=empty_detail)

    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Txt file must be UTF-8 encoded.") from exc
