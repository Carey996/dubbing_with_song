from __future__ import annotations

import os
import re
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..config import ConfigError, require_env


class LlmSegment(BaseModel):
    type: Literal["narration", "lyric"] = Field(description="Segment type.")
    text: str = Field(description="Original text for this segment.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence for the segment classification.")
    reason: str = Field(description="Short Chinese explanation for the classification.")
    durationSec: float | None = Field(default=None, ge=0.5, description="Recommended duration in seconds.")
    songClipStartSec: float | None = Field(default=None, ge=0.0, description="Optional song clip start time.")
    songClipEndSec: float | None = Field(default=None, ge=0.0, description="Optional song clip end time.")


class LlmSongCandidate(BaseModel):
    title: str = Field(description="Guessed song title, or 待确认歌曲 if unknown.")
    artist: str = Field(description="Guessed artist, or 待确认歌手 if unknown.")
    matchedLyrics: list[str] = Field(default_factory=list, description="Lyrics lines that triggered this candidate.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence for the song candidate.")
    note: str = Field(description="Short Chinese note for user confirmation.")


class LlmAnalysis(BaseModel):
    segments: list[LlmSegment] = Field(description="Ordered narration and lyric segments.")
    songCandidates: list[LlmSongCandidate] = Field(default_factory=list, description="Possible songs found from lyric text.")


def analyze_text(text: str) -> dict:
    try:
        api_key = require_env("OPENAI_API_KEY")
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        from langchain_core.output_parsers import PydanticOutputParser
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="LangChain analyzer requires langchain-openai. Install project dependencies first.",
        ) from exc

    cleaned = normalize_text(text)
    if not cleaned:
        return build_analysis([], [], engine="langchain")

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

    parser = PydanticOutputParser(pydantic_object=LlmAnalysis)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "\n".join(
                    [
                        "你是一个中文有声内容制作助理。",
                        "任务：把输入文本拆成适合配音和歌曲替换的连续段落。",
                        "只按原文顺序分段，不要改写原文，不要补写正文。",
                        "把普通叙述、对话、旁白标为 narration。",
                        "把明显歌词、歌名提示、角色正在唱的内容、可被歌曲片段替换的段落标为 lyric。",
                        "如果能从歌词或书名号推测歌曲，给 songCandidates；不确定时用 待确认歌曲/待确认歌手。",
                        "durationSec 需要给前端默认时间轴使用；中文旁白按自然语速估算，歌词至少 4 秒。",
                        "reason 和 note 用简短中文。",
                        "必须只输出 JSON，不要输出 Markdown，不要输出解释。",
                        "{format_instructions}",
                    ]
                ),
            ),
            ("human", "{text}"),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    llm = ChatOpenAI(**llm_kwargs)
    try:
        result = (prompt | llm | parser).invoke({"text": cleaned})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LangChain analyzer failed: {exc}") from exc

    return normalize_llm_analysis(result, cleaned)


def normalize_llm_analysis(result: LlmAnalysis, source_text: str) -> dict:
    llm_segments = result.segments or []
    if not llm_segments:
        llm_segments = [LlmSegment(type="narration", text=source_text, confidence=0.5, reason="LLM 未返回分段，按旁白处理")]

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
        segments.append(
            {
                "id": f"seg-{len(segments) + 1:03d}",
                "index": len(segments),
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

    return build_analysis(segments, candidates, engine="langchain")


def build_analysis(segments: list[dict], candidates: list[dict], engine: str) -> dict:
    return {
        "analysisEngine": engine,
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
