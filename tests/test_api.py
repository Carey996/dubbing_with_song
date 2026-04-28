from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.prompts import ChatPromptTemplate

from backend.app.main import app
from backend.app.routers import projects
from backend.app.services import analyzer
from backend.app.services import renderer
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
        "chapters": [],
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
    prompt = analyzer.build_prompt(ChatPromptTemplate, analyzer.get_format_instructions())
    rendered = prompt.format(text="“江宇滚出娱乐圈！”")

    assert "观众喊话、辱骂、口号" in rendered
    assert "必须标为 narration" in rendered


def test_parse_llm_analysis_drops_empty_segment_objects():
    content = '{"segments":[{"type":"narration","text":"正文。"},{}],"songCandidates":[]}'
    parsed = analyzer.parse_llm_analysis(content)

    assert len(parsed.segments) == 1
    assert parsed.segments[0].text == "正文。"


def test_split_text_into_chapters_detects_common_headings():
    text = "第一章 登台\n内容一。\n\n## 第二章 唱歌\n内容二。"
    chapters = split_text_into_chapters(text)

    assert [chapter.title for chapter in chapters] == ["第一章 登台", "## 第二章 唱歌"]


def test_split_text_into_chapters_drops_punctuation_only_preface():
    text = "------------\n\n……\n！！！\n第一章 登台\n内容一。"
    chapters = split_text_into_chapters(text)

    assert [chapter.title for chapter in chapters] == ["第一章 登台"]
    assert [chapter.text for chapter in chapters] == ["内容一。"]


def test_split_text_into_chapters_drops_punctuation_only_text():
    chapters = split_text_into_chapters("------------\n……\n！！！")

    assert chapters == []


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


def test_empty_chapter_analysis_fallback_keeps_target_chapter():
    chapters = [TextChapter(id="chap-002", title="第二章", text="内容二")]

    merged = analyzer.merge_llm_analyses([(chapters[0], analyzer.LlmAnalysis(segments=[]))], chapters, "内容二")

    assert merged["segments"][0]["chapterId"] == "chap-002"
    assert merged["segments"][0]["chapterTitle"] == "第二章"


def test_chapters_endpoint_exposes_project_chapters():
    created = client.post("/api/projects", json={"text": "第一章 登台\n内容一。\n\n第二章 唱歌\n内容二。"})
    response = client.get(f"/api/projects/{created.json()['id']}/chapters")

    assert response.status_code == 200
    assert [chapter["title"] for chapter in response.json()["chapters"]] == ["第一章 登台", "第二章 唱歌"]
    assert [chapter["text"] for chapter in response.json()["chapters"]] == ["内容一。", "内容二。"]


def test_chapter_analyze_endpoint_uses_selected_chapter(monkeypatch):
    captured = {}

    def fake_chapter_analysis(text: str, chapters=None):
        captured["text"] = text
        captured["chapters"] = chapters
        return fake_analysis(text)

    monkeypatch.setattr(projects, "analyze_text", fake_chapter_analysis)
    created = client.post("/api/projects", json={"text": "第一章 登台\n内容一。\n\n第二章 唱歌\n内容二。"})
    response = client.post(f"/api/projects/{created.json()['id']}/chapters/chap-002/analyze")

    assert response.status_code == 200
    assert captured["text"] == "内容二。"
    assert captured["chapters"][0].id == "chap-002"


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


def test_find_ffmpeg_prefers_configured_path(monkeypatch, tmp_path):
    ffmpeg_path = tmp_path / "ffmpeg.exe"
    ffmpeg_path.write_text("fake ffmpeg", encoding="utf-8")
    monkeypatch.setenv("FFMPEG_PATH", str(ffmpeg_path))

    assert renderer.find_ffmpeg() == str(ffmpeg_path)


def test_synthesize_wav_uses_openai_speech_provider(monkeypatch, tmp_path):
    output_path = tmp_path / "speech.wav"
    calls = []

    class FakeSpeech:
        def create(self, **kwargs):
            calls.append(kwargs)
            return self

        def write_to_file(self, path):
            Path(path).write_bytes(b"RIFFfakeWAVE")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append({"client": kwargs})
            self.audio = type("Audio", (), {"speech": FakeSpeech()})()

    monkeypatch.setenv("TTS_PROVIDER", "openai")
    monkeypatch.setenv("TTS_API_KEY", "local-key")
    monkeypatch.setenv("TTS_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("TTS_MODEL", "local-tts")
    monkeypatch.setenv("TTS_VOICE", "local-voice")
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)

    renderer.synthesize_wav("测试旁白", output_path)

    assert output_path.read_bytes() == b"RIFFfakeWAVE"
    assert calls[0]["client"] == {"api_key": "local-key", "base_url": "http://127.0.0.1:9999/v1"}
    assert calls[1]["model"] == "local-tts"
    assert calls[1]["voice"] == "local-voice"
    assert calls[1]["input"] == "测试旁白"
    assert calls[1]["response_format"] == "wav"


def test_synthesize_wav_requires_ai_tts_config(monkeypatch, tmp_path):
    monkeypatch.delenv("TTS_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("测试旁白", tmp_path / "speech.wav")

    assert exc.value.status_code == 503
    assert "TTS_API_KEY" in str(exc.value.detail)
