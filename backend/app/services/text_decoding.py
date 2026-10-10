"""Decode user-supplied text files that are not necessarily UTF-8.

Chinese sources still ship GBK/GB2312 ``.txt`` and ``.lrc`` files, and a strict UTF-8 decode
rejected perfectly readable input. The order below is deliberate rather than "try everything":

1. A byte-order mark decides the encoding outright. UTF-16 must only be attempted here: arbitrary
   even-length bytes decode "successfully" as UTF-16, so guessing it would turn every UTF-8 file
   into mojibake.
2. UTF-8 strict, because that is the encoding this project writes.
3. Configurable fallbacks, ``gb18030`` by default. GB18030 covers GBK and GB2312 *and* can decode
   most byte sequences, so it runs last and its result has to look like text.
"""

from __future__ import annotations

import codecs
import os
import unicodedata

DEFAULT_FALLBACK_ENCODINGS = ("gb18030",)
FALLBACK_ENCODINGS_ENV = "TEXT_FALLBACK_ENCODINGS"
PRIMARY_ENCODING = "utf-8-sig"
# A byte-order mark has to be matched longest-first: the UTF-32-LE mark starts with the
# UTF-16-LE mark, so checking UTF-16 first would misread every UTF-32-LE file.
BOM_ENCODINGS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
# Decoded text may contain at most this fraction of control/private/unassigned characters before
# it is treated as binary that merely happened to decode.
MAX_SUSPICIOUS_RATIO = 0.01


class TextDecodingError(ValueError):
    """The bytes could not be decoded into plausible text."""

    def __init__(self, message: str, *, tried: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.tried = tried


def fallback_encodings() -> tuple[str, ...]:
    configured = (os.getenv(FALLBACK_ENCODINGS_ENV) or "").strip()
    if not configured:
        return DEFAULT_FALLBACK_ENCODINGS
    names = tuple(part.strip() for part in configured.split(",") if part.strip())
    return names or DEFAULT_FALLBACK_ENCODINGS


def bom_encoding(raw: bytes) -> str | None:
    for bom, encoding in BOM_ENCODINGS:
        if raw.startswith(bom):
            return encoding
    return None


def decode_text(raw: bytes) -> tuple[str, str]:
    """Decode ``raw`` and return ``(text, encoding)``.

    Raises ``TextDecodingError`` when nothing plausible matches, so callers can turn it into a 400
    without leaking codec internals.
    """

    if not raw:
        raise TextDecodingError("The file is empty.")

    bom = bom_encoding(raw)
    if bom is not None:
        try:
            text = raw.decode(bom)
        except UnicodeDecodeError as exc:
            raise TextDecodingError(
                f"The file declares the {bom} byte-order mark but its content is corrupt: {exc}",
                tried=(bom,),
            ) from exc
        if not looks_like_text(text):
            raise TextDecodingError(
                f"The file declares the {bom} byte-order mark but does not decode to text.",
                tried=(bom,),
            )
        return text, bom

    tried: list[str] = []
    for encoding in (PRIMARY_ENCODING, *fallback_encodings()):
        tried.append(encoding)
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # One rule for every candidate: a real script is not made of control, private-use or
        # unassigned characters. UTF-8 can decode binary too (any byte below 0x80 is valid), so
        # trusting it blindly would let a .docx through as "text".
        if not looks_like_text(text):
            continue
        return text, encoding

    raise TextDecodingError(build_failure_message(tried), tried=tuple(tried))


def looks_like_text(text: str) -> bool:
    if not text.strip():
        return False
    suspicious = sum(1 for char in text if is_suspicious(char))
    return suspicious <= len(text) * MAX_SUSPICIOUS_RATIO


def is_suspicious(char: str) -> bool:
    if char in "\t\n\r\f\v":
        return False
    return unicodedata.category(char) in {"Cc", "Cs", "Co", "Cn"}


def build_failure_message(tried: list[str] | tuple[str, ...]) -> str:
    attempted = " / ".join(tried) or PRIMARY_ENCODING
    return (
        f"Could not decode the file as text (tried {attempted}). "
        "Re-save it as UTF-8, or set "
        f"{FALLBACK_ENCODINGS_ENV} to the legacy encoding it uses."
    )
