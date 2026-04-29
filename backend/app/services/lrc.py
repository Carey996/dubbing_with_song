from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from fastapi import HTTPException


@dataclass(frozen=True)
class LrcLine:
    index: int
    start_sec: float
    text: str


TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")


def parse_lrc(content: str) -> list[LrcLine]:
    lines: list[LrcLine] = []
    for raw_line in (content or "").splitlines():
        matches = list(TIMESTAMP_RE.finditer(raw_line))
        if not matches:
            continue
        text = TIMESTAMP_RE.sub("", raw_line).strip()
        if not text:
            continue
        for match in matches:
            lines.append(LrcLine(index=len(lines), start_sec=parse_timestamp(match), text=text))

    lines.sort(key=lambda item: item.start_sec)
    for index, line in enumerate(lines):
        lines[index] = LrcLine(index=index, start_sec=line.start_sec, text=line.text)
    if not lines:
        raise HTTPException(status_code=400, detail="LRC file must contain timestamped lyric lines.")
    return lines


def parse_timestamp(match: re.Match[str]) -> float:
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = match.group(3) or "0"
    fraction_seconds = int(fraction) / (10 ** len(fraction))
    return round(minutes * 60 + seconds + fraction_seconds, 3)


def enrich_analysis_with_lrc(analysis: dict, lrc_lines: list[LrcLine]) -> dict:
    if not lrc_lines:
        return analysis

    enriched = copy.deepcopy(analysis)
    segments = enriched.get("segments") or []
    for segment in segments:
        if segment.get("type") != "lyric":
            continue
        match = find_best_lrc_match(str(segment.get("text") or ""), lrc_lines)
        if not match:
            continue
        line, score = match
        next_line = next((candidate for candidate in lrc_lines if candidate.index > line.index), None)
        default_end = line.start_sec + max(0.5, float(segment.get("durationSec", 2.0) or 2.0))
        end_sec = next_line.start_sec if next_line and next_line.start_sec > line.start_sec else default_end
        segment["songClipStartSec"] = round(line.start_sec, 2)
        segment["songClipEndSec"] = round(end_sec, 2)
        segment["lyricMatch"] = {
            "source": "chapter-lrc",
            "lineIndex": line.index,
            "line": line.text,
            "startSec": round(line.start_sec, 2),
            "endSec": round(end_sec, 2),
            "confidence": round(score, 3),
        }

    if isinstance(enriched.get("timeline"), dict):
        enriched["timeline"]["segments"] = segments
    return enriched


def find_best_lrc_match(text: str, lrc_lines: list[LrcLine]) -> tuple[LrcLine, float] | None:
    normalized_text = normalize_lyric_text(text)
    if not normalized_text:
        return None

    best_line: LrcLine | None = None
    best_score = 0.0
    for line in lrc_lines:
        normalized_line = normalize_lyric_text(line.text)
        if not normalized_line:
            continue
        if normalized_line in normalized_text or normalized_text in normalized_line:
            score = 1.0
        else:
            score = SequenceMatcher(None, normalized_text, normalized_line).ratio()
        if score > best_score:
            best_line = line
            best_score = score

    if not best_line or best_score < 0.45:
        return None
    return best_line, best_score


def normalize_lyric_text(text: str) -> str:
    return "".join(char.lower() for char in text or "" if char.isalnum())
