from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.prompts import ChatPromptTemplate

from backend.app.main import app
from backend.app.routers import projects
from backend.app.repositories import db
from backend.app.services import analyzer
from backend.app.services import project_store
from backend.app.services import renderer
from backend.app.services.chapter_service import TextChapter, split_text_into_chapters
from backend.app.services.project_store import list_projects


client = TestClient(app)
TEST_TMP_DIR = Path("pytest-cache-files-ai-tts")


def test_project_store_keeps_sql_out_of_service_layer():
    source = Path(project_store.__file__).read_text(encoding="utf-8")

    assert "conn.execute" not in source
    assert "transaction()" not in source
    assert "from .db import" not in source
    assert "db.initialize_database" not in source


def test_repository_sql_is_loaded_from_sql_files():
    repository_dir = Path(project_store.__file__).parents[1] / "repositories"
    for path in repository_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "CREATE TABLE" not in source
        assert "SELECT *" not in source
        assert "INSERT INTO" not in source
        assert "UPDATE projects" not in source

    sql_files = {path.name for path in (repository_dir / "sql").glob("*.sql")}
    assert "schema.sql" in sql_files
    assert "list_projects.sql" in sql_files
    assert "save_analysis.sql" in sql_files


def test_project_creation_writes_sqlite_metadata():
    created = client.post("/api/projects", json={"text": "第一章 登台\n内容一。"})
    assert created.status_code == 200

    projects_list = list_projects()

    assert any(item["id"] == created.json()["id"] for item in projects_list)
    saved = next(item for item in projects_list if item["id"] == created.json()["id"])
    assert saved["title"] == "第一章 登台"
    assert saved["status"] == "created"
    assert saved["textLength"] == len("第一章 登台\n内容一。")
    assert saved["hasAnalysis"] is False
    assert saved["hasSong"] is False
    assert saved["hasTimeline"] is False
    assert saved["hasRender"] is False


def test_database_schema_version_is_initialized():
    db.initialize_database()
    with db.connect() as conn:
        value = conn.execute("select value from schema_meta where key = 'schema_version'").fetchone()[0]

    assert value == "1"


def test_projects_endpoint_lists_persisted_projects():
    created = client.post("/api/projects", json={"text": "历史项目内容。"})

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert any(item["id"] == created.json()["id"] for item in response.json()["projects"])


def test_project_delete_removes_project_record_and_files():
    created = client.post("/api/projects", json={"text": "待删除历史项目。"})
    project_id = created.json()["id"]
    project_root = project_store.PROJECTS_DIR / project_id
    output_root = project_store.OUTPUTS_DIR / project_id
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "demo.wav").write_bytes(b"RIFFfakeWAVE")

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 200
    assert response.json() == {"id": project_id, "deleted": True}
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert not project_root.exists()
    assert not output_root.exists()
    assert all(item["id"] != project_id for item in client.get("/api/projects").json()["projects"])


def test_project_delete_returns_404_for_missing_project():
    response = client.delete("/api/projects/missing-project")

    assert response.status_code == 404


def test_existing_project_folder_is_imported():
    project_id = "legacyabc123"
    root = project_store.PROJECTS_DIR / project_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "source.txt").write_text("旧项目正文。", encoding="utf-8")
    project_store.write_json(root / "metadata.json", {"id": project_id, "createdAt": "2026-04-29T00:00:00+00:00"})

    project_store.import_existing_projects()
    response = client.get(f"/api/projects/{project_id}")

    assert response.status_code == 200
    assert response.json()["id"] == project_id
    assert response.json()["title"] == "旧项目正文。"
    assert response.json()["text"] == "旧项目正文。"


def test_full_analysis_is_persisted_with_history(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    project_id = client.post("/api/projects", json={"text": "旁白内容。"}).json()["id"]

    analyzed = client.post(f"/api/projects/{project_id}/analyze")
    history = client.get(f"/api/projects/{project_id}/analyses")
    detail = client.get(f"/api/projects/{project_id}")

    assert analyzed.status_code == 200
    assert history.status_code == 200
    assert history.json()["analyses"][0]["scope"] == "all"
    assert history.json()["analyses"][0]["status"] == "succeeded"
    assert detail.json()["analysis"]["analysisEngine"] == "test-double"
    assert detail.json()["timeline"]["segments"]


def test_chapter_analysis_is_persisted_with_scope(monkeypatch):
    def fake_chapter_analysis(text: str, chapters=None):
        return fake_analysis(text)

    monkeypatch.setattr(projects, "analyze_text", fake_chapter_analysis)
    project_id = client.post("/api/projects", json={"text": "第一章 登台\n内容一。\n\n第二章 唱歌\n内容二。"}).json()["id"]

    response = client.post(f"/api/projects/{project_id}/chapters/chap-002/analyze")
    history = client.get(f"/api/projects/{project_id}/analyses")

    assert response.status_code == 200
    assert history.json()["analyses"][0]["scope"] == "chapter"
    assert history.json()["analyses"][0]["chapterId"] == "chap-002"
    assert history.json()["analyses"][0]["chapterTitle"] == "第二章 唱歌"


def test_song_and_timeline_updates_are_reflected_in_project_list(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    project_id = client.post("/api/projects", json={"text": "旁白内容。"}).json()["id"]
    timeline = client.post(f"/api/projects/{project_id}/analyze").json()["timeline"]
    timeline["bgmVolume"] = 0.42
    client.patch(f"/api/projects/{project_id}/timeline", json=timeline)
    client.post(
        f"/api/projects/{project_id}/song-file",
        files={"file": ("demo.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00", "audio/mpeg")},
    )

    listed = client.get("/api/projects").json()["projects"]
    item = next(row for row in listed if row["id"] == project_id)
    detail = client.get(f"/api/projects/{project_id}").json()

    assert item["hasSong"] is True
    assert item["hasTimeline"] is True
    assert detail["timeline"]["bgmVolume"] == 0.42


def test_render_result_is_persisted_with_history(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    project_id = client.post("/api/projects", json={"text": "旁白内容。"}).json()["id"]
    client.post(f"/api/projects/{project_id}/analyze")

    def fake_render(project):
        output_dir = project.output_dir
        output_path = output_dir / "narration.wav"
        output_path.write_bytes(b"RIFFfakeWAVE")
        return {
            "status": "narration-only",
            "message": "测试生成完成。",
            "outputUrl": project.output_url(output_path),
            "warnings": [],
        }

    monkeypatch.setattr(projects, "render_project", fake_render)
    rendered = client.post(f"/api/projects/{project_id}/render")
    history = client.get(f"/api/projects/{project_id}/renders")
    detail = client.get(f"/api/projects/{project_id}")

    assert rendered.status_code == 200
    assert history.json()["renders"][0]["status"] == "succeeded"
    assert history.json()["renders"][0]["outputUrl"]
    assert f"/outputs/{project_id}/" in history.json()["renders"][0]["outputUrl"]
    assert detail.json()["latestRender"]["message"] == "测试生成完成。"


def test_render_requires_uploaded_song_for_lyric_segments(monkeypatch):
    project_id = client.post("/api/projects", json={"text": "第一章\n她开始唱歌。"}).json()["id"]
    timeline = {
        "bgmVolume": 0.5,
        "narrationVolume": 1,
        "songStartSec": 0,
        "segments": [
            {
                "id": "seg-001",
                "index": 0,
                "type": "lyric",
                "text": "一闪一闪亮晶晶",
                "startSec": 0,
                "durationSec": 4,
                "confidence": 0.9,
                "reason": "测试歌词",
                "songClipStartSec": 12,
                "songClipEndSec": 16,
            }
        ],
    }
    client.patch(f"/api/projects/{project_id}/timeline", json=timeline)
    monkeypatch.setattr(renderer, "synthesize_wav", lambda *_args, **_kwargs: pytest.fail("lyrics must not be sent to TTS"))

    response = client.post(f"/api/projects/{project_id}/render")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "歌词片段" in detail
    assert "MP3" in detail


def test_mix_with_song_uses_lyric_segment_clip_cues(monkeypatch, tmp_path):
    narration_path = tmp_path / "narration.wav"
    song_path = tmp_path / "song.mp3"
    output_path = tmp_path / "mixed.mp3"
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(renderer, "get_wav_duration", lambda _path: 8.0)
    monkeypatch.setattr(renderer.subprocess, "run", fake_run)

    renderer.mix_with_song(
        "ffmpeg.exe",
        narration_path,
        song_path,
        output_path,
        {
            "bgmVolume": 0.42,
            "songStartSec": 0,
            "segments": [
                {
                    "id": "seg-001",
                    "type": "lyric",
                    "startSec": 2.5,
                    "durationSec": 3,
                    "songClipStartSec": 10,
                    "songClipEndSec": 13,
                }
            ],
        },
    )

    command = captured["command"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "atrim=start=10.000:duration=3.000" in filter_graph
    assert "volume=0.42" in filter_graph
    assert "adelay=2500:all=1" in filter_graph


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
    assert normalized["segments"][0]["speakerName"] == "旁白"
    assert normalized["segments"][0]["speakerGender"] == "unknown"
    assert normalized["segments"][0]["emotion"] == "neutral"
    assert normalized["segments"][0]["voiceStyle"] == "neutral_narrator"
    assert normalized["segments"][0]["delivery"]


def test_llm_analysis_preserves_segment_voice_metadata():
    result = analyzer.LlmAnalysis.model_validate(
        {
            "segments": [
                {
                    "type": "narration",
                    "text": "沈红鱼低声说：“别过去。”",
                    "speakerName": "沈红鱼",
                    "speakerGender": "female",
                    "emotion": "tense",
                    "voiceStyle": "young_female_soft",
                    "delivery": "压低声音，语速略快，带紧张感。",
                }
            ]
        }
    )

    normalized = analyzer.normalize_llm_analysis([], result, "沈红鱼低声说：“别过去。”")
    segment = normalized["segments"][0]

    assert segment["speakerName"] == "沈红鱼"
    assert segment["speakerGender"] == "female"
    assert segment["emotion"] == "tense"
    assert segment["voiceStyle"] == "young_female_soft"
    assert segment["delivery"] == "压低声音，语速略快，带紧张感。"


def test_llm_prompt_keeps_audience_chants_as_narration():
    prompt = analyzer.build_prompt(ChatPromptTemplate, analyzer.get_format_instructions())
    rendered = prompt.format(text="“江宇滚出娱乐圈！”")

    assert "观众喊话、辱骂、口号" in rendered
    assert "必须标为 narration" in rendered


def test_llm_prompt_limits_segments_per_chunk():
    prompt = analyzer.build_prompt(ChatPromptTemplate, analyzer.get_format_instructions())
    rendered = prompt.format(text="第一句。第二句。第三句。")

    assert "每个分块最多输出 12 个 segments" in rendered
    assert "连续 narration 尽量合并" in rendered


def test_llm_prompt_requests_speaker_emotion_and_voice_fields():
    prompt = analyzer.build_prompt(ChatPromptTemplate, analyzer.get_format_instructions())
    rendered = prompt.format(text="沈红鱼低声说：“别过去。” 江宇怒吼：“让开！”")

    assert "speakerName" in rendered
    assert "speakerGender" in rendered
    assert "emotion" in rendered
    assert "voiceStyle" in rendered
    assert "delivery" in rendered


def test_parse_llm_analysis_drops_empty_segment_objects():
    content = '{"segments":[{"type":"narration","text":"正文。"},{}],"songCandidates":[]}'
    parsed = analyzer.parse_llm_analysis(content)

    assert len(parsed.segments) == 1
    assert parsed.segments[0].text == "正文。"


def test_parse_llm_analysis_accepts_missing_segment_comma():
    content = """
    {
      "segments": [
        {"type": "narration", "text": "第一段。"}
        {"type": "lyric", "text": "一闪一闪亮晶晶", "confidence": 0.8}
      ],
      "songCandidates": []
    }
    """
    parsed = analyzer.parse_llm_analysis(content)

    assert [segment.text for segment in parsed.segments] == ["第一段。", "一闪一闪亮晶晶"]
    assert parsed.segments[1].type == "lyric"


def test_parse_llm_analysis_salvages_complete_segments_from_truncated_tail():
    content = """
    {
      "segments": [
        {"type": "narration", "text": "完整段落。"},
        {"type": "narration", "text": "没写完的段落"
    """
    parsed = analyzer.parse_llm_analysis(content)

    assert len(parsed.segments) == 1
    assert parsed.segments[0].text == "完整段落。"


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


def test_timeline_update_preserves_voice_metadata():
    project_id = client.post("/api/projects", json={"text": "沈红鱼低声说：“别过去。”"}).json()["id"]
    timeline = {
        "songStartSec": 0,
        "bgmVolume": 0.2,
        "segments": [
            {
                "id": "seg-001",
                "index": 0,
                "type": "narration",
                "text": "沈红鱼低声说：“别过去。”",
                "startSec": 0,
                "durationSec": 3,
                "confidence": 0.9,
                "reason": "角色对白",
                "speakerName": "沈红鱼",
                "speakerGender": "female",
                "emotion": "tense",
                "voiceStyle": "young_female_soft",
                "delivery": "压低声音，语速略快，带紧张感。",
            }
        ],
    }

    updated = client.patch(f"/api/projects/{project_id}/timeline", json=timeline)
    segment = updated.json()["segments"][0]

    assert segment["speakerName"] == "沈红鱼"
    assert segment["speakerGender"] == "female"
    assert segment["emotion"] == "tense"
    assert segment["voiceStyle"] == "young_female_soft"
    assert segment["delivery"] == "压低声音，语速略快，带紧张感。"


def test_song_upload_accepts_mp3_file():
    project_id = client.post("/api/projects", json={"text": "旁白内容。"}).json()["id"]
    content = b"ID3\x03\x00\x00\x00\x00\x00\x00"
    uploaded = client.post(
        f"/api/projects/{project_id}/song-file",
        files={"file": ("demo.mp3", content, "audio/mpeg")},
    )
    downloaded = client.get(f"/api/projects/{project_id}/song-file")

    assert uploaded.status_code == 200
    assert uploaded.json()["filename"] == "demo.mp3"
    assert downloaded.status_code == 200
    assert downloaded.content == content


def test_find_ffmpeg_prefers_bundled_path(monkeypatch):
    TEST_TMP_DIR.mkdir(exist_ok=True)
    bundled_path = TEST_TMP_DIR / "bundled-ffmpeg.exe"
    bundled_path.write_text("fake bundled ffmpeg", encoding="utf-8")
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.setattr(renderer, "BUNDLED_FFMPEG_PATH", bundled_path)
    monkeypatch.setattr(renderer.shutil, "which", lambda executable: None)

    assert renderer.find_ffmpeg() == str(bundled_path)


def test_find_ffmpeg_allows_configured_override(monkeypatch):
    TEST_TMP_DIR.mkdir(exist_ok=True)
    bundled_path = TEST_TMP_DIR / "bundled-ffmpeg.exe"
    configured_path = TEST_TMP_DIR / "configured-ffmpeg.exe"
    bundled_path.write_text("fake bundled ffmpeg", encoding="utf-8")
    configured_path.write_text("fake configured ffmpeg", encoding="utf-8")
    monkeypatch.setattr(renderer, "BUNDLED_FFMPEG_PATH", bundled_path)
    monkeypatch.setenv("FFMPEG_PATH", str(configured_path))

    assert renderer.find_ffmpeg() == str(configured_path)


def test_synthesize_wav_uses_openai_speech_provider(monkeypatch):
    TEST_TMP_DIR.mkdir(exist_ok=True)
    output_path = TEST_TMP_DIR / "speech.wav"
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
    monkeypatch.delenv("TTS_INSTRUCTIONS", raising=False)
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)

    renderer.synthesize_wav("测试旁白", output_path)

    assert output_path.read_bytes() == b"RIFFfakeWAVE"
    assert calls[0]["client"] == {"api_key": "local-key", "base_url": "http://127.0.0.1:9999/v1"}
    assert calls[1]["model"] == "local-tts"
    assert calls[1]["voice"] == "local-voice"
    assert calls[1]["input"] == "测试旁白"
    assert calls[1]["response_format"] == "wav"
    assert calls[1]["instructions"] == renderer.DEFAULT_TTS_INSTRUCTIONS


def test_build_narration_track_passes_voice_metadata_to_tts(monkeypatch):
    TEST_TMP_DIR.mkdir(exist_ok=True)
    chunks_dir = TEST_TMP_DIR / "voice-chunks"
    output_path = TEST_TMP_DIR / "voice-narration.wav"
    received_segments = []

    def fake_synthesize(text, path, segment=None):
        received_segments.append(segment)
        path.parent.mkdir(parents=True, exist_ok=True)
        renderer.create_silence_wav(path, 0.1, (1, 2, 22050))

    monkeypatch.setattr(renderer, "synthesize_wav", fake_synthesize)
    timeline = {
        "segments": [
            {
                "id": "seg-001",
                "type": "narration",
                "text": "江宇怒吼：“让开！”",
                "durationSec": 1,
                "speakerName": "江宇",
                "speakerGender": "male",
                "emotion": "angry",
                "voiceStyle": "young_male_bright",
                "delivery": "提高音量，语气急促愤怒。",
            }
        ]
    }

    renderer.build_narration_track(timeline, chunks_dir, output_path, include_lyrics=True)

    assert received_segments[0]["speakerName"] == "江宇"
    assert received_segments[0]["speakerGender"] == "male"
    assert received_segments[0]["emotion"] == "angry"
    assert received_segments[0]["delivery"] == "提高音量，语气急促愤怒。"


def test_synthesize_wav_applies_segment_voice_and_delivery(monkeypatch):
    TEST_TMP_DIR.mkdir(exist_ok=True)
    output_path = TEST_TMP_DIR / "speech-segment-style.wav"
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
    monkeypatch.setenv("TTS_MODEL", "local-tts")
    monkeypatch.setenv("TTS_VOICE_MALE", "onyx")
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)

    renderer.synthesize_wav(
        "江宇怒吼：“让开！”",
        output_path,
        segment={
            "speakerName": "江宇",
            "speakerGender": "male",
            "emotion": "angry",
            "voiceStyle": "young_male_bright",
            "delivery": "提高音量，语气急促愤怒。",
        },
    )

    assert calls[1]["voice"] == "onyx"
    assert "江宇" in calls[1]["instructions"]
    assert "male" in calls[1]["instructions"]
    assert "angry" in calls[1]["instructions"]
    assert "提高音量，语气急促愤怒。" in calls[1]["instructions"]


def test_gemini_tts_input_uses_emotion_style_tags(monkeypatch):
    monkeypatch.delenv("TTS_INSTRUCTIONS", raising=False)

    styled = renderer.build_tts_input(
        "沈红鱼低声说：“别过去。”",
        "google/gemini-3.1-flash-tts-preview",
        {"emotion": "tense", "delivery": "压低声音，语速略快。"},
    )

    assert styled == "[whispers] 沈红鱼低声说：“别过去。”"


def test_blank_tts_instructions_disables_gemini_style_tags(monkeypatch):
    monkeypatch.setenv("TTS_INSTRUCTIONS", "   ")

    styled = renderer.build_tts_input(
        "沈红鱼低声说：“别过去。”",
        "google/gemini-3.1-flash-tts-preview",
        {"emotion": "tense", "delivery": "压低声音，语速略快。"},
    )

    assert styled == "沈红鱼低声说：“别过去。”"


def test_synthesize_wav_allows_tts_instruction_override(monkeypatch):
    TEST_TMP_DIR.mkdir(exist_ok=True)
    output_path = TEST_TMP_DIR / "speech-style.wav"
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
    monkeypatch.setenv("TTS_MODEL", "local-tts")
    monkeypatch.setenv("TTS_INSTRUCTIONS", "用克制但带紧张感的悬疑旁白朗读。")
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)

    renderer.synthesize_wav("门外传来脚步声。", output_path)

    assert calls[1]["instructions"] == "用克制但带紧张感的悬疑旁白朗读。"


def test_blank_tts_instructions_disables_instruction_field(monkeypatch):
    TEST_TMP_DIR.mkdir(exist_ok=True)
    output_path = TEST_TMP_DIR / "speech-no-style.wav"
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
    monkeypatch.setenv("TTS_MODEL", "local-tts")
    monkeypatch.setenv("TTS_INSTRUCTIONS", "   ")
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)

    renderer.synthesize_wav("普通旁白。", output_path)

    assert "instructions" not in calls[1]


def test_synthesize_wav_uses_openrouter_mp3_and_ffmpeg(monkeypatch):
    TEST_TMP_DIR.mkdir(exist_ok=True)
    output_path = TEST_TMP_DIR / "openrouter.wav"
    calls = []

    class FakeSpeech:
        def create(self, **kwargs):
            calls.append(kwargs)
            return self

        def write_to_file(self, path):
            Path(path).write_bytes(b"fake mp3")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append({"client": kwargs})
            self.audio = type("Audio", (), {"speech": FakeSpeech()})()

    def fake_convert(ffmpeg, input_path, converted_path):
        calls.append({"convert": (ffmpeg, input_path.name, converted_path.name)})
        converted_path.write_bytes(b"RIFFfakeWAVE")

    monkeypatch.setenv("TTS_PROVIDER", "openrouter")
    monkeypatch.setenv("TTS_API_KEY", "openrouter-key")
    monkeypatch.delenv("TTS_BASE_URL", raising=False)
    monkeypatch.delenv("TTS_MODEL", raising=False)
    monkeypatch.delenv("TTS_RESPONSE_FORMAT", raising=False)
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(renderer, "find_ffmpeg", lambda: "ffmpeg.exe")
    monkeypatch.setattr(renderer, "convert_audio_to_wav", fake_convert)

    renderer.synthesize_wav("测试旁白", output_path)

    assert output_path.read_bytes() == b"RIFFfakeWAVE"
    assert calls[0]["client"] == {"api_key": "openrouter-key", "base_url": "https://openrouter.ai/api/v1"}
    assert calls[1]["model"] == "openai/gpt-4o-mini-tts-2025-12-15"
    assert calls[1]["voice"] == "alloy"
    assert calls[1]["response_format"] == "mp3"
    assert calls[2]["convert"] == ("ffmpeg.exe", "openrouter.mp3", "openrouter.wav")


def test_openrouter_tts_requires_ffmpeg(monkeypatch):
    TEST_TMP_DIR.mkdir(exist_ok=True)
    output_path = TEST_TMP_DIR / "openrouter-no-ffmpeg.wav"

    class FakeSpeech:
        def create(self, **kwargs):
            return self

        def write_to_file(self, path):
            Path(path).write_bytes(b"fake mp3")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.audio = type("Audio", (), {"speech": FakeSpeech()})()

    monkeypatch.setenv("TTS_PROVIDER", "openrouter")
    monkeypatch.setenv("TTS_API_KEY", "openrouter-key")
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(renderer, "find_ffmpeg", lambda: None)

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("测试旁白", output_path)

    assert exc.value.status_code == 503
    assert "ffmpeg" in str(exc.value.detail)


def test_openrouter_tts_requires_mp3_response_format(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "openrouter")
    monkeypatch.setenv("TTS_API_KEY", "openrouter-key")
    monkeypatch.setenv("TTS_RESPONSE_FORMAT", "pcm")

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("测试旁白", TEST_TMP_DIR / "openrouter-pcm.wav")

    assert exc.value.status_code == 503
    assert "TTS_RESPONSE_FORMAT=mp3" in str(exc.value.detail)


def test_openrouter_voxtral_tts_rejects_chinese_text_before_remote_call(monkeypatch):
    calls = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setenv("TTS_PROVIDER", "openrouter")
    monkeypatch.setenv("TTS_API_KEY", "openrouter-key")
    monkeypatch.setenv("TTS_MODEL", "mistralai/voxtral-mini-tts-2603")
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("这是一段中文旁白。", TEST_TMP_DIR / "voxtral-chinese.wav")

    assert exc.value.status_code == 503
    assert "mistralai/voxtral-mini-tts-2603" in str(exc.value.detail)
    assert "中文" in str(exc.value.detail)
    assert calls == []


def test_openrouter_provider_404_has_actionable_detail(monkeypatch):
    class FakeOpenRouterError(Exception):
        status_code = 404

        def __str__(self):
            return "Error code: 404 - {'error': {'message': 'No successful provider responses.', 'code': 404}}"

    class FakeSpeech:
        def create(self, **kwargs):
            raise FakeOpenRouterError()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.audio = type("Audio", (), {"speech": FakeSpeech()})()

    monkeypatch.setenv("TTS_PROVIDER", "openrouter")
    monkeypatch.setenv("TTS_API_KEY", "openrouter-key")
    monkeypatch.setenv("TTS_MODEL", "openai/gpt-4o-mini-tts-2025-12-15")
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("English narration.", TEST_TMP_DIR / "openrouter-404.wav")

    assert exc.value.status_code == 503
    detail = str(exc.value.detail)
    assert "OpenRouter TTS provider returned 404" in detail
    assert "TTS_MODEL=openai/gpt-4o-mini-tts-2025-12-15" in detail
    assert "No successful provider responses" in detail


def test_openrouter_provider_403_has_actionable_detail(monkeypatch):
    class FakeOpenRouterError(Exception):
        status_code = 403

        def __str__(self):
            return "Error code: 403 - {'error': {'message': 'The request is prohibited due to a violation of provider Terms Of Service.', 'code': 403}}"

    class FakeSpeech:
        def create(self, **kwargs):
            raise FakeOpenRouterError()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.audio = type("Audio", (), {"speech": FakeSpeech()})()

    monkeypatch.setenv("TTS_PROVIDER", "openrouter")
    monkeypatch.setenv("TTS_API_KEY", "openrouter-key")
    monkeypatch.setattr(renderer, "OpenAI", FakeOpenAI)

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("English narration.", TEST_TMP_DIR / "openrouter-403.wav")

    assert exc.value.status_code == 503
    detail = str(exc.value.detail)
    assert "OpenRouter TTS provider rejected this input" in detail
    assert "lyric" in detail


def test_synthesize_wav_requires_ai_tts_config(monkeypatch):
    monkeypatch.delenv("TTS_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("测试旁白", TEST_TMP_DIR / "speech.wav")

    assert exc.value.status_code == 503
    assert "TTS_API_KEY" in str(exc.value.detail)
