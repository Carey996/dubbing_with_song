from __future__ import annotations

from pathlib import Path

from backend.app.cli import run


TEST_INPUT_DIR = Path("data/test-inputs")


def write_test_txt(filename: str, text: str) -> Path:
    TEST_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    source = TEST_INPUT_DIR / filename
    source.write_text(text, encoding="utf-8")
    return source


def test_cli_creates_project_from_txt():
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
