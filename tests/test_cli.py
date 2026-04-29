from __future__ import annotations

from pathlib import Path

from backend.app import cli
from backend.app.cli import run


TEST_INPUT_DIR = Path("data/test-inputs")


def fake_analysis(text: str, chapters=None) -> dict:
    segments = [
        {
            "id": "seg-001",
            "index": 0,
            "type": "lyric" if "小星星" in text else "narration",
            "text": text,
            "startSec": 0.0,
            "durationSec": 5.0,
            "confidence": 0.9,
            "reason": "测试替身",
            "songClipStartSec": 0.0,
            "songClipEndSec": 5.0 if "小星星" in text else 0.0,
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
    ] if "小星星" in text else []
    return {
        "analysisEngine": "test-double",
        "chapters": [chapter.public_dict() for chapter in chapters] if chapters else [],
        "segments": segments,
        "songCandidates": candidates,
        "timeline": {
            "bgmVolume": 0.22,
            "narrationVolume": 1.0,
            "songStartSec": 0.0,
            "segments": segments,
        },
    }


def write_test_txt(filename: str, text: str) -> Path:
    TEST_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    source = TEST_INPUT_DIR / filename
    source.write_text(text, encoding="utf-8")
    return source


def test_cli_creates_project_from_txt(monkeypatch):
    monkeypatch.setattr(cli, "analyze_text", fake_analysis)
    source = write_test_txt("cli-story-with-lyrics.txt", "他听见歌声。\n\n《小星星》\n一闪一闪亮晶晶")

    payload = run([str(source)])

    assert payload["project"]["id"]
    assert payload["project"]["textLength"] > 0
    assert payload["analysis"]["segmentCount"] >= 1
    assert payload["analysis"]["songCandidates"][0]["title"] == "小星星"


def test_cli_can_skip_analysis():
    source = write_test_txt("cli-story-narration.txt", "只有旁白。")

    payload = run([str(source), "--no-analyze"])

    assert payload["project"]["id"]
    assert "analysis" not in payload


def test_cli_created_project_is_listed(monkeypatch):
    monkeypatch.setattr(cli, "analyze_text", fake_analysis)
    source = write_test_txt("cli-persisted-story.txt", "CLI 持久化正文。")

    payload = run([str(source), "--no-analyze"])
    from backend.app.services.project_store import list_projects

    assert any(item["id"] == payload["project"]["id"] for item in list_projects())
