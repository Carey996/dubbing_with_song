from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from backend.app.main import app
from backend.app.routers import projects
from backend.app.services import analyzer
from backend.app.services.chapter_service import TextChapter, split_text_into_chapters


client = TestClient(app)


def fake_analysis(text: str) -> dict:
    if "小星星" in text:
        segments = [
            {
                "id": "seg-001",
                "index": 0,
                "type": "lyric",
                "text": text,
                "startSec": 0.0,
                "durationSec": 5.0,
                "confidence": 0.9,
                "reason": "测试替身识别为歌词",
                "songClipStartSec": 0.0,
                "songClipEndSec": 5.0,
            }
        ]
        candidates = [
            {
                "title": "小星星",
                "artist": "待确认歌手",
                "matchedLyrics": ["一闪一闪亮晶晶"],
                "confidence": 0.9,
                "note": "测试替身歌曲候选",
            }
        ]
    else:
        segments = [
            {
                "id": "seg-001",
                "index": 0,
                "type": "narration",
                "text": text,
                "startSec": 0.0,
                "durationSec": 3.0,
                "confidence": 0.9,
                "reason": "测试替身识别为旁白",
                "songClipStartSec": 0.0,
                "songClipEndSec": 0.0,
            }
        ]
        candidates = []

    return {
        "analysisEngine": "test-double",
        "segments": segments,
        "songCandidates": candidates,
        "timeline": {
            "bgmVolume": 0.22,
            "narrationVolume": 1.0,
            "songStartSec": 0.0,
            "segments": segments,
        },
    }


def test_analyze_endpoint_uses_configured_analyzer(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    created = client.post("/api/projects", json={"text": "这是一个普通旁白。这里继续讲故事。"})
    assert created.status_code == 200
    project_id = created.json()["id"]

    analyzed = client.post(f"/api/projects/{project_id}/analyze")
    assert analyzed.status_code == 200
    payload = analyzed.json()
    assert payload["analysisEngine"] == "test-double"


def test_analyzer_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        analyzer.analyze_text("测试文本")
    assert exc.value.status_code == 503
    assert "OPENAI_API_KEY" in str(exc.value.detail)


def test_llm_analysis_accepts_missing_optional_segment_fields():
    result = analyzer.LlmAnalysis.model_validate(
        {
            "segments": [
                {
                    "type": "narration",
                    "text": "沈红鱼看着舞台上的江宇，",
                }
            ]
        }
    )
    normalized = analyzer.normalize_llm_analysis([], result, "沈红鱼看着舞台上的江宇，")

    assert normalized["segments"][0]["confidence"] == 0.6
    assert normalized["segments"][0]["reason"] == "LLM 未提供原因"


def test_llm_prompt_keeps_audience_chants_as_narration():
    parser = PydanticOutputParser(pydantic_object=analyzer.LlmAnalysis)
    prompt = analyzer.build_prompt(ChatPromptTemplate, parser)
    rendered = prompt.format(text="“江宇滚出娱乐圈！”")

    assert "观众喊话、辱骂、口号" in rendered
    assert "必须标为 narration" in rendered


def test_split_text_into_chapters_detects_common_headings():
    text = "第一章 登台\n内容一。\n\n## 第二章 唱歌\n内容二。"
    chapters = split_text_into_chapters(text)

    assert [chapter.title for chapter in chapters] == ["第一章 登台", "## 第二章 唱歌"]


def test_merge_llm_analyses_preserves_chapter_metadata():
    chapters = [
        TextChapter(id="chap-001", title="第一章", text="内容一"),
        TextChapter(id="chap-002", title="第二章", text="内容二"),
    ]
    first = analyzer.LlmAnalysis(segments=[analyzer.LlmSegment(type="narration", text="内容一")])
    second = analyzer.LlmAnalysis(segments=[analyzer.LlmSegment(type="narration", text="内容二")])

    merged = analyzer.merge_llm_analyses([(chapters[0], first), (chapters[1], second)], chapters, "内容一\n内容二")

    assert [segment["chapterId"] for segment in merged["segments"]] == ["chap-001", "chap-002"]
    assert [chapter["title"] for chapter in merged["chapters"]] == ["第一章", "第二章"]


def test_chapters_endpoint_exposes_project_chapters():
    created = client.post("/api/projects", json={"text": "第一章 登台\n内容一。\n\n第二章 唱歌\n内容二。"})
    response = client.get(f"/api/projects/{created.json()['id']}/chapters")

    assert response.status_code == 200
    assert [chapter["title"] for chapter in response.json()["chapters"]] == ["第一章 登台", "第二章 唱歌"]


def test_plain_narration_flow(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    created = client.post("/api/projects", json={"text": "这是一个普通旁白。这里继续讲故事。"})
    assert created.status_code == 200
    project_id = created.json()["id"]

    analyzed = client.post(f"/api/projects/{project_id}/analyze")
    assert analyzed.status_code == 200
    payload = analyzed.json()
    assert payload["segments"]
    assert payload["songCandidates"] == []


def test_project_creation_is_json_only():
    created = client.post(
        "/api/projects",
        files={"file": ("demo.txt", b"demo", "text/plain")},
    )
    assert created.status_code == 422


def test_project_creation_from_txt_file_uses_separate_endpoint():
    created = client.post(
        "/api/projects/text-file",
        files={"file": ("demo.txt", "文件里的旁白内容。".encode("utf-8"), "text/plain")},
    )
    assert created.status_code == 200
    project = client.get(f"/api/projects/{created.json()['id']}").json()
    assert project["text"] == "文件里的旁白内容。"


def test_lyric_detection_and_timeline_update(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    text = "他停下来听见有人唱。\n\n《小星星》\n一闪一闪亮晶晶\n满天都是小星星"
    project_id = client.post("/api/projects", json={"text": text}).json()["id"]
    analysis = client.post(f"/api/projects/{project_id}/analyze").json()

    assert any(segment["type"] == "lyric" for segment in analysis["segments"])
    assert analysis["songCandidates"][0]["title"] == "小星星"

    timeline = analysis["timeline"]
    timeline["bgmVolume"] = 0.3
    updated = client.patch(f"/api/projects/{project_id}/timeline", json=timeline)
    assert updated.status_code == 200
    assert updated.json()["bgmVolume"] == 0.3


def test_song_upload_accepts_mp3_file():
    project_id = client.post("/api/projects", json={"text": "旁白内容。"}).json()["id"]
    uploaded = client.post(
        f"/api/projects/{project_id}/song-file",
        files={"file": ("demo.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00", "audio/mpeg")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["filename"] == "demo.mp3"
