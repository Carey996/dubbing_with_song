from __future__ import annotations

import os
import json
import re
from typing import Literal

from fastapi import HTTPException
from pydantic import ValidationError
from pydantic import BaseModel, Field

from ..config import ConfigError, require_env
from .chapter_service import TextChapter, split_text_into_chapters

DEFAULT_CHUNK_CHARS = 1200


class LlmSegment(BaseModel):
    type: Literal["narration", "lyric"] = Field(description="Segment type.")
    text: str = Field(description="Original text for this segment.")
    confidence: float = Field(default=0.6, ge=0.0, le=1.0, description="Confidence for the segment classification.")
    reason: str = Field(default="LLM 未提供原因", description="Short Chinese explanation for the classification.")
    durationSec: float | None = Field(default=None, ge=0.5, description="Recommended duration in seconds.")
    songClipStartSec: float | None = Field(default=None, ge=0.0, description="Optional song clip start time.")
    songClipEndSec: float | None = Field(default=None, ge=0.0, description="Optional song clip end time.")


class LlmSongCandidate(BaseModel):
    title: str = Field(default="待确认歌曲", description="Guessed song title, or 待确认歌曲 if unknown.")
    artist: str = Field(default="待确认歌手", description="Guessed artist, or 待确认歌手 if unknown.")
    matchedLyrics: list[str] = Field(default_factory=list, description="Lyrics lines that triggered this candidate.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence for the song candidate.")
    note: str = Field(default="LLM 推测，需用户确认。", description="Short Chinese note for user confirmation.")


class LlmAnalysis(BaseModel):
    segments: list[LlmSegment] = Field(description="Ordered narration and lyric segments.")
    songCandidates: list[LlmSongCandidate] = Field(default_factory=list, description="Possible songs found from lyric text.")


def analyze_text(text: str, chapters: list[TextChapter] | None = None) -> dict:
    try:
        api_key = require_env("OPENAI_API_KEY")
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="LangChain analyzer requires langchain-openai. Install project dependencies first.",
        ) from exc

    cleaned = normalize_text(text)
    if not cleaned:
        return build_analysis([], [], engine="langchain", chapters=[])

    model = os.getenv("OPENAI_MODEL", "gpt-5-nano")
    base_url = os.getenv("OPENAI_BASE_URL") or None
    llm_kwargs = {
        "model": model,
        "api_key": api_key,
        "temperature": 0,
        "max_retries": 2,
    }
    if base_url:
        llm_kwargs["base_url"] = base_url

    prompt = build_prompt(ChatPromptTemplate, get_format_instructions())
    llm = ChatOpenAI(**llm_kwargs)
    target_chapters = chapters or split_text_into_chapters(cleaned)
    chunk_inputs = build_chunk_inputs(target_chapters, int(os.getenv("ANALYZER_CHUNK_CHARS", DEFAULT_CHUNK_CHARS)))
    try:
        analyzed_chunks = [
            (chapter, parse_llm_analysis((prompt | llm).invoke({"text": chunk}).content))
            for chapter, chunk in chunk_inputs
        ]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LangChain analyzer failed: {exc}") from exc

    return merge_llm_analyses(analyzed_chunks, target_chapters, cleaned)


def build_prompt(chat_prompt_template, format_instructions: str):
    return chat_prompt_template.from_messages(
        [
            (
                "system",
                "\n".join(
                    [
                        "你是一个中文有声内容制作助理。",
                        "任务：把输入文本拆成适合配音和歌曲替换的连续段落。",
                        "只按原文顺序分段，不要改写原文，不要补写正文。",
                        "输入中以“章节：”或“分块：”开头的行只是上下文元信息，不能输出到任何 segment.text。",
                        "把普通叙述、对话、旁白标为 narration。",
                        "每个分块最多输出 12 个 segments；连续 narration 尽量合并为 2 到 5 句话一段。",
                        "只有真实歌曲歌词、歌名提示、明确在唱歌且可用歌曲片段替换的内容，才能标为 lyric。",
                        "观众喊话、辱骂、口号、弹幕、普通台词、角色对白都必须标为 narration，不能标为 lyric。",
                        "如果能从歌词或书名号推测歌曲，给 songCandidates；不确定时用 待确认歌曲/待确认歌手。",
                        "durationSec 需要给前端默认时间轴使用；中文旁白按自然语速估算，歌词至少 4 秒。",
                        "confidence 和 reason 必须为每个 segment 提供；如果不确定，confidence 用 0.6。",
                        "reason 和 note 用简短中文。",
                        "必须只输出 JSON，不要输出 Markdown，不要输出解释。",
                        "{format_instructions}",
                    ]
                ),
            ),
            ("human", "{text}"),
        ]
    ).partial(format_instructions=format_instructions)


def get_format_instructions() -> str:
    return """
输出必须是一个 JSON object，结构如下：
{
  "segments": [
    {
      "type": "narration 或 lyric",
      "text": "原文片段",
      "confidence": 0.0 到 1.0,
      "reason": "简短中文原因",
      "durationSec": 秒数,
      "songClipStartSec": 0,
      "songClipEndSec": 秒数
    }
  ],
  "songCandidates": [
    {
      "title": "歌曲名或待确认歌曲",
      "artist": "歌手或待确认歌手",
      "matchedLyrics": ["命中的歌词"],
      "confidence": 0.0 到 1.0,
      "note": "简短中文说明"
    }
  ]
}
不要在 segments 中输出空对象。不要省略 type 和 text。
""".strip()


def parse_llm_analysis(content: str) -> LlmAnalysis:
    try:
        payload = json.loads(extract_json_object(content))
    except json.JSONDecodeError as exc:
        payload = parse_relaxed_llm_payload(content)
        if not payload.get("segments"):
            raise ValueError(f"LLM returned invalid JSON: {exc}") from exc

    payload["segments"] = [
        segment
        for segment in payload.get("segments", [])
        if isinstance(segment, dict) and segment.get("type") and segment.get("text")
    ]
    payload["songCandidates"] = [
        candidate
        for candidate in payload.get("songCandidates", [])
        if isinstance(candidate, dict)
    ]

    try:
        return LlmAnalysis.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"LLM JSON did not match expected schema: {exc}") from exc


def parse_relaxed_llm_payload(content: str) -> dict:
    stripped = strip_json_fence(content)
    return {
        "segments": parse_named_object_array(stripped, "segments"),
        "songCandidates": parse_named_object_array(stripped, "songCandidates"),
    }


def parse_named_object_array(content: str, field_name: str) -> list[dict]:
    match = re.search(rf'"{re.escape(field_name)}"\s*:\s*\[', content)
    if not match:
        return []

    array_start = match.end()
    array_end = find_matching_bracket(content, array_start - 1)
    array_text = content[array_start:array_end]
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    cursor = 0

    while cursor < len(array_text):
        object_start = find_next_unquoted_char(array_text, "{", cursor)
        if object_start == -1:
            break
        try:
            item, cursor = decoder.raw_decode(array_text, object_start)
        except json.JSONDecodeError:
            break
        if isinstance(item, dict):
            objects.append(item)

    return objects


def find_matching_bracket(content: str, open_index: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for index in range(open_index, len(content)):
        char = content[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
    return len(content)


def find_next_unquoted_char(content: str, target: str, start: int) -> int:
    in_string = False
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string and char == target:
            return index
    return -1


def extract_json_object(content: str) -> str:
    stripped = strip_json_fence(content)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("No JSON object found", stripped, 0)
    return stripped[start:end + 1]


def strip_json_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped


def split_text_for_llm(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    units = [unit.strip() for unit in re.split(r"(\n\s*\n)", text) if unit.strip()]
    if len(units) <= 1:
        units = [unit.strip() for unit in re.split(r"(?<=[。！？!?])", text) if unit.strip()]

    chunks: list[str] = []
    current = ""
    for unit in units:
        separator = "\n\n" if current else ""
        if current and len(current) + len(separator) + len(unit) > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = f"{current}{separator}{unit}" if current else unit

    if current:
        chunks.append(current)
    return chunks


def build_chunk_inputs(chapters: list[TextChapter], max_chars: int) -> list[tuple[TextChapter, str]]:
    inputs: list[tuple[TextChapter, str]] = []
    for chapter in chapters:
        chunks = split_text_for_llm(chapter.text, max_chars)
        for chunk_index, chunk in enumerate(chunks):
            chunk_header = f"章节：{chapter.title}"
            if len(chunks) > 1:
                chunk_header = f"{chunk_header}\n分块：{chunk_index + 1}/{len(chunks)}"
            inputs.append((chapter, f"{chunk_header}\n\n{chunk}"))
    return inputs


def merge_llm_analyses(analyzed_chunks: list[tuple[TextChapter, LlmAnalysis]], chapters: list[TextChapter], source_text: str) -> dict:
    if not analyzed_chunks:
        return normalize_llm_analysis([], LlmAnalysis(segments=[]), source_text, chapters)

    merged_segments: list[LlmSegment] = []
    segment_chapters: list[TextChapter] = []
    merged_candidates: list[LlmSongCandidate] = []
    seen_candidates: set[tuple[str, str]] = set()

    for chapter, result in analyzed_chunks:
        merged_segments.extend(result.segments)
        segment_chapters.extend([chapter] * len(result.segments))
        for candidate in result.songCandidates:
            key = (candidate.title.strip(), candidate.artist.strip())
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            merged_candidates.append(candidate)

    return normalize_llm_analysis(segment_chapters, LlmAnalysis(segments=merged_segments, songCandidates=merged_candidates), source_text, chapters)


def normalize_llm_analysis(
    segment_chapters: list[TextChapter],
    result: LlmAnalysis,
    source_text: str,
    chapters: list[TextChapter] | None = None,
) -> dict:
    llm_segments = result.segments or []
    if not llm_segments:
        llm_segments = [LlmSegment(type="narration", text=source_text, confidence=0.5, reason="LLM 未返回分段，按旁白处理")]
        fallback_chapters = chapters or split_text_into_chapters(source_text)
        segment_chapters = [fallback_chapters[0]] if fallback_chapters else []

    if len(segment_chapters) < len(llm_segments):
        fallback_chapters = chapters or split_text_into_chapters(source_text)
        default_chapter = segment_chapters[-1] if segment_chapters else fallback_chapters[0]
        segment_chapters = [*segment_chapters, *([default_chapter] * (len(llm_segments) - len(segment_chapters)))]

    segments = []
    cursor = 0.0
    for index, item in enumerate(llm_segments):
        text = normalize_text(item.text)
        if not text:
            continue
        duration = round(float(item.durationSec or estimate_duration(text, item.type)), 2)
        duration = max(0.5, duration)
        song_clip_start = max(0.0, float(item.songClipStartSec or 0.0))
        song_clip_end = float(item.songClipEndSec) if item.songClipEndSec is not None else duration
        if item.type == "narration":
            song_clip_start = 0.0
            song_clip_end = 0.0
        chapter = segment_chapters[index]
        segments.append(
            {
                "id": f"seg-{len(segments) + 1:03d}",
                "index": len(segments),
                "chapterId": chapter.id,
                "chapterTitle": chapter.title,
                "type": item.type,
                "text": text,
                "startSec": round(cursor, 2),
                "durationSec": duration,
                "confidence": round(float(item.confidence), 3),
                "reason": item.reason or "LLM 分析",
                "songClipStartSec": round(song_clip_start, 2),
                "songClipEndSec": round(max(song_clip_start, song_clip_end), 2),
            }
        )
        cursor += duration

    candidates = [
        {
            "title": candidate.title or "待确认歌曲",
            "artist": candidate.artist or "待确认歌手",
            "matchedLyrics": candidate.matchedLyrics,
            "confidence": round(float(candidate.confidence), 3),
            "note": candidate.note or "LLM 推测，需用户确认。",
        }
        for candidate in result.songCandidates
    ]

    return build_analysis(segments, candidates, engine="langchain", chapters=chapters or segment_chapters)


def build_analysis(segments: list[dict], candidates: list[dict], engine: str, chapters: list[TextChapter]) -> dict:
    return {
        "analysisEngine": engine,
        "chapters": [chapter.public_dict() for chapter in chapters],
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


def estimate_duration(text: str, kind: str) -> float:
    visible_chars = len(re.sub(r"\s+", "", text))
    if kind == "lyric":
        return round(max(4.0, visible_chars / 4.8), 2)
    return round(max(2.2, visible_chars / 6.5), 2)
