from __future__ import annotations

import re
from dataclasses import dataclass


LYRIC_HINTS = (
    "副歌",
    "歌词",
    "唱",
    "哼",
    "music",
    "song",
    "chorus",
    "verse",
)


@dataclass(frozen=True)
class DraftSegment:
    text: str
    kind: str
    confidence: float
    reason: str


def analyze_text(text: str) -> dict:
    cleaned = normalize_text(text)
    blocks = split_blocks(cleaned)
    draft_segments = [classify_block(block) for block in blocks]
    segments = []
    cursor = 0.0

    for index, segment in enumerate(draft_segments):
        duration = estimate_duration(segment.text, segment.kind)
        segments.append(
            {
                "id": f"seg-{index + 1:03d}",
                "index": index,
                "type": segment.kind,
                "text": segment.text,
                "startSec": round(cursor, 2),
                "durationSec": duration,
                "confidence": segment.confidence,
                "reason": segment.reason,
                "songClipStartSec": 0.0,
                "songClipEndSec": duration if segment.kind == "lyric" else 0.0,
            }
        )
        cursor += duration

    lyric_text = "\n".join(item.text for item in draft_segments if item.kind == "lyric")
    candidates = build_song_candidates(lyric_text)

    return {
        "segments": segments,
        "songCandidates": candidates,
        "timeline": {
            "bgmVolume": 0.22,
            "narrationVolume": 1.0,
            "songStartSec": 0.0,
            "segments": segments,
        },
    }


def normalize_text(text: str) -> str:
    return re.sub(r"\r\n?", "\n", text or "").strip()


def split_blocks(text: str) -> list[str]:
    if not text:
        return []

    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) > 1:
        return blocks

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) > 1:
        return lines

    return [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", text) if part.strip()]


def classify_block(block: str) -> DraftSegment:
    line_count = len([line for line in block.split("\n") if line.strip()])
    punctuation_count = len(re.findall(r"[，。！？,.!?；;：:]", block))
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", block))
    latin_words = len(re.findall(r"[A-Za-z]+", block))
    has_hint = any(hint.lower() in block.lower() for hint in LYRIC_HINTS)
    has_quote = bool(re.search(r"[《「『“\"]", block))
    short_lines = line_count >= 2 and all(len(line.strip()) <= 32 for line in block.split("\n") if line.strip())
    repeated_phrase = bool(re.search(r"(.{2,8})\1", block))

    lyric_score = 0
    if has_hint:
        lyric_score += 3
    if short_lines:
        lyric_score += 2
    if has_quote:
        lyric_score += 1
    if repeated_phrase:
        lyric_score += 1
    if punctuation_count <= max(1, line_count) and chinese_chars + latin_words > 8:
        lyric_score += 1

    if lyric_score >= 3:
        return DraftSegment(block, "lyric", min(0.92, 0.48 + lyric_score * 0.1), "疑似歌词结构或包含歌曲提示词")

    return DraftSegment(block, "narration", 0.82, "按普通叙述文本处理")


def estimate_duration(text: str, kind: str) -> float:
    visible_chars = len(re.sub(r"\s+", "", text))
    if kind == "lyric":
        return round(max(4.0, visible_chars / 4.8), 2)
    return round(max(2.2, visible_chars / 6.5), 2)


def build_song_candidates(lyric_text: str) -> list[dict]:
    if not lyric_text.strip():
        return []

    title_match = re.search(r"[《「『“\"]([^》」』”\"]{1,40})[》」』”\"]", lyric_text)
    title = title_match.group(1) if title_match else "待确认歌曲"
    lines = [line.strip() for line in lyric_text.splitlines() if line.strip()]
    matched = lines[:3] if lines else [lyric_text[:40]]

    return [
        {
            "title": title,
            "artist": "待确认歌手",
            "matchedLyrics": matched,
            "confidence": 0.64 if title_match else 0.42,
            "note": "MVP 根据歌词结构和书名号提示生成候选，需用户上传对应 MP3 后确认。",
        }
    ]
