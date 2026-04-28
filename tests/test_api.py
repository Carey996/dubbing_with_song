from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_plain_narration_flow():
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


def test_lyric_detection_and_timeline_update():
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
