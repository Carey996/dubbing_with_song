"""Encoding compatibility for uploaded text.

Chinese sources still ship GBK ``.txt``/``.lrc`` files, so decoding has to accept them without
turning UTF-8 into mojibake or letting binary content through.
"""

from __future__ import annotations

import codecs

import pytest

from backend.app.services import text_decoding
from backend.app.services.text_decoding import TextDecodingError, decode_text


CHINESE = "第一章 夜航\n夜里十一点，港口的灯一盏一盏熄灭。\n"
LRC_LINE = "[00:12.00]一闪一闪亮晶晶"


def decode(raw: bytes) -> tuple[str, str]:
    return decode_text(raw)


# --------------------------------------------------------------------------------------
# UTF-8 stays the primary path
# --------------------------------------------------------------------------------------


def test_plain_utf8_decodes_as_utf8():
    text, encoding = decode(CHINESE.encode("utf-8"))

    assert text == CHINESE
    assert encoding == "utf-8-sig"


def test_utf8_bom_is_stripped():
    text, encoding = decode(codecs.BOM_UTF8 + CHINESE.encode("utf-8"))

    assert text == CHINESE
    assert encoding == "utf-8-sig"
    assert not text.startswith("\ufeff")


def test_ascii_utf8_is_not_reported_as_a_fallback_encoding():
    # GB18030 also decodes pure ASCII, so the primary encoding has to win.
    text, encoding = decode(b"plain ascii text\n")

    assert text == "plain ascii text\n"
    assert encoding == "utf-8-sig"


def test_utf8_is_never_misread_as_utf16():
    # UTF-16 without a BOM "decodes" any even-length byte string, so this is the regression that
    # would silently turn every Chinese upload into mojibake.
    text, encoding = decode(CHINESE.encode("utf-8"))

    assert text == CHINESE
    assert encoding != "utf-16"


# --------------------------------------------------------------------------------------
# GBK / GB2312 / GB18030
# --------------------------------------------------------------------------------------


def test_gbk_content_is_accepted():
    text, encoding = decode(CHINESE.encode("gbk"))

    assert text == CHINESE
    assert encoding == "gb18030"


def test_gb2312_content_is_accepted():
    text, encoding = decode("第一章 夜航".encode("gb2312"))

    assert text == "第一章 夜航"
    assert encoding == "gb18030"


def test_gbk_lrc_line_is_accepted():
    text, encoding = decode(LRC_LINE.encode("gbk"))

    assert text == LRC_LINE
    assert encoding == "gb18030"


def test_gb18030_four_byte_sequences_are_accepted():
    sample = "扩展汉字：𠀀𠀁"
    text, encoding = decode(sample.encode("gb18030"))

    assert text == sample
    assert encoding == "gb18030"


def test_gbk_text_with_ascii_punctuation_and_newlines_round_trips():
    sample = "Line 1: 旁白。\nLine 2: 歌词，标点！？\n"
    text, _encoding = decode(sample.encode("gbk"))

    assert text == sample


# --------------------------------------------------------------------------------------
# BOM-marked UTF-16 / UTF-32
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bom, encoding_name",
    [
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ],
)
def test_utf16_with_bom_is_accepted(bom, encoding_name):
    text, encoding = decode(bom + CHINESE.encode(encoding_name))

    assert text == CHINESE
    assert encoding == "utf-16"


@pytest.mark.parametrize("encoding_name", ["utf-32-le", "utf-32-be"])
def test_utf32_with_bom_is_accepted(encoding_name):
    bom = codecs.BOM_UTF32_LE if encoding_name == "utf-32-le" else codecs.BOM_UTF32_BE
    text, encoding = decode(bom + CHINESE.encode(encoding_name))

    assert text == CHINESE
    assert encoding == "utf-32"


def test_bom_detection_matches_the_longest_mark_first():
    # The UTF-32-LE mark begins with the UTF-16-LE mark.
    assert text_decoding.bom_encoding(codecs.BOM_UTF32_LE + b"\x00\x00\x00\x00") == "utf-32"
    assert text_decoding.bom_encoding(codecs.BOM_UTF16_LE + b"a\x00") == "utf-16"
    assert text_decoding.bom_encoding(codecs.BOM_UTF8 + b"x") == "utf-8-sig"
    assert text_decoding.bom_encoding(b"no bom here") is None


def test_a_truncated_utf16_file_reports_the_bom_problem():
    with pytest.raises(TextDecodingError) as exc:
        decode(codecs.BOM_UTF16_LE + b"\x00")

    assert "byte-order mark" in exc.value.message
    assert exc.value.tried == ("utf-16",)


# --------------------------------------------------------------------------------------
# binary and empty input stay rejected
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        bytes(range(256)),
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
        b"\x00\x01\x02\x03binary\x00data",
        b"\x1b[0m\x07\x08\x0ecolour codes\x01\x02",
    ],
)
def test_binary_content_is_rejected(raw):
    with pytest.raises(TextDecodingError) as exc:
        decode(raw)

    assert "Could not decode" in exc.value.message
    assert "utf-8-sig" in exc.value.message
    assert "gb18030" in exc.value.message
    assert "TEXT_FALLBACK_ENCODINGS" in exc.value.message


def test_empty_and_whitespace_only_content_is_rejected():
    for raw in (b"", b"   ", b"\n\n\t"):
        with pytest.raises(TextDecodingError):
            decode(raw)


def test_gb18030_decodable_but_implausible_bytes_are_rejected():
    # GB18030 can decode almost any byte sequence; the plausibility check is what stops binary
    # content from arriving as a page of CJK.
    raw = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    with pytest.raises(TextDecodingError):
        decode_text(raw)

    assert not text_decoding.looks_like_text(raw.decode("gb18030"))


def test_an_explicit_byte_order_mark_is_trusted():
    # 0xFF 0xFE at the start *is* the UTF-16-LE mark, so these five bytes are read as two (odd)
    # characters rather than as binary. Being explicit in the file is the user's call.
    text, encoding = decode(codecs.BOM_UTF16_LE + b"\x00bad")

    assert encoding == "utf-16"
    assert len(text) == 2


def test_a_utf8_nul_is_rejected_like_any_other_control_character():
    raw = "旁白\x00内容".encode("utf-8")

    with pytest.raises(TextDecodingError):
        decode(raw)


# --------------------------------------------------------------------------------------
# plausibility helper
# --------------------------------------------------------------------------------------


def test_looks_like_text_accepts_normal_content():
    assert text_decoding.looks_like_text(CHINESE) is True
    assert text_decoding.looks_like_text("Line 1: 旁白。\n") is True


def test_looks_like_text_rejects_control_heavy_content():
    assert text_decoding.looks_like_text("\x01\x02\x03abc") is False
    assert text_decoding.looks_like_text("a\tb\nc\rd") is True
    assert text_decoding.looks_like_text("   ") is False


def test_is_suspicious_covers_control_private_and_unassigned_codes():
    assert text_decoding.is_suspicious("\x00") is True
    assert text_decoding.is_suspicious("\ue000") is True  # private use
    assert text_decoding.is_suspicious("\n") is False
    assert text_decoding.is_suspicious("汉") is False
    assert text_decoding.is_suspicious("A") is False


# --------------------------------------------------------------------------------------
# configurable fallbacks
# --------------------------------------------------------------------------------------


def test_fallback_encodings_default_to_gb18030(monkeypatch):
    monkeypatch.delenv("TEXT_FALLBACK_ENCODINGS", raising=False)

    assert text_decoding.fallback_encodings() == ("gb18030",)


def test_fallback_encodings_can_be_overridden(monkeypatch):
    monkeypatch.setenv("TEXT_FALLBACK_ENCODINGS", "big5, shift_jis")

    assert text_decoding.fallback_encodings() == ("big5", "shift_jis")


def test_fallback_encodings_ignores_blank_configuration(monkeypatch):
    monkeypatch.setenv("TEXT_FALLBACK_ENCODINGS", "  ,  ")

    assert text_decoding.fallback_encodings() == ("gb18030",)


def test_big5_is_accepted_when_configured(monkeypatch):
    sample = "第一章 夜航"
    monkeypatch.setenv("TEXT_FALLBACK_ENCODINGS", "big5")

    text, encoding = decode(sample.encode("big5"))

    assert text == sample
    assert encoding == "big5"


def test_gb18030_wins_for_gbk_even_when_big5_is_also_listed(monkeypatch):
    # Documented ambiguity: GB18030 is listed first and decodes GBK correctly, so a caller who
    # needs Big5 must configure it *instead of* gb18030.
    monkeypatch.setenv("TEXT_FALLBACK_ENCODINGS", "gb18030,big5")

    text, encoding = decode(CHINESE.encode("gbk"))

    assert text == CHINESE
    assert encoding == "gb18030"


def test_an_unknown_fallback_encoding_is_skipped(monkeypatch):
    monkeypatch.setenv("TEXT_FALLBACK_ENCODINGS", "not-a-codec,gb18030")

    text, encoding = decode(CHINESE.encode("gbk"))

    assert text == CHINESE
    assert encoding == "gb18030"
