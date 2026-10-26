from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph

from backend.app.main import app
from backend.app.services import analyzer, project_store
from backend.app.workflows import analysis_nodes, registry, render, runner
from backend.app.workflows.common import WorkflowError, step_node
from backend.app.workflows.runner import merge_state_update, run_registry
from backend.app.workflows.state import WorkflowState


client = TestClient(app)
TERMINAL = {"succeeded", "failed", "interrupted"}
EXPECTED_WORKFLOW_IDS = {
    "project-intake",
    "chapter-split",
    "content-analysis",
    "chapter-analysis",
    "song-alignment",
    "timeline-edit",
    "render",
}


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def wait_for_run(run_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    snapshot = run_registry.get(run_id).snapshot()
    while time.time() < deadline:
        snapshot = run_registry.get(run_id).snapshot()
        if snapshot["status"] in TERMINAL:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s: {snapshot}")


def run_workflow(workflow_id: str, inputs: dict, expect_status: int = 202) -> dict:
    response = client.post(f"/api/workflows/{workflow_id}/runs", json=inputs)
    assert response.status_code == expect_status, response.text
    return wait_for_run(response.json()["runId"])


def invoke_graph(workflow_id: str, inputs: dict) -> tuple[dict, list[dict]]:
    """Run a graph directly with an event recorder, without the HTTP layer."""

    events: list[dict] = []
    graph = registry.get_graph(workflow_id)
    state = graph.invoke(
        {
            "runId": "unit",
            "workflowId": workflow_id,
            "inputs": inputs,
            "data": {},
            "steps": [],
            "warnings": [],
            "error": None,
        },
        config={"configurable": {"emit": lambda event_type, **fields: events.append({"type": event_type, **fields})}},
    )
    return state, events


def node_names(state: dict) -> list[str]:
    return [step["node"] for step in state["steps"]]


def create_project(text: str) -> str:
    response = client.post("/api/projects", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def patch_timeline(project_id: str, segments: list[dict], **settings) -> dict:
    payload = {
        "bgmVolume": 0.22,
        "narrationVolume": 1.0,
        "songStartSec": 0.0,
        "segments": segments,
        **settings,
    }
    response = client.patch(f"/api/projects/{project_id}/timeline", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def narration_segment(text: str = "旁白内容。", duration: float = 3.0) -> dict:
    return {"id": "seg-001", "index": 0, "type": "narration", "text": text, "durationSec": duration}


def lyric_segment(text: str = "一闪一闪亮晶晶", duration: float = 5.0) -> dict:
    return {
        "id": "seg-001",
        "index": 0,
        "type": "lyric",
        "text": text,
        "durationSec": duration,
        "songClipStartSec": 0.0,
        "songClipEndSec": duration,
    }


class FakeStructuredResult:
    """Stand-in for the runnable ``with_structured_output`` returns.

    It decodes the responder's JSON and validates it into ``LlmAnalysis``, so an invalid answer
    raises ``ValueError`` exactly like the real structured-output parser would.
    """

    def __init__(self, responder) -> None:
        self.responder = responder

    def invoke(self, payload: dict) -> analyzer.LlmAnalysis:
        return analyzer.LlmAnalysis.model_validate(json.loads(self.responder(payload["text"])))


class FakeStructuredLLM:
    """A fake chat model that only implements the structured-output surface the analyzer uses."""

    def __init__(self, responder) -> None:
        self.responder = responder
        self.structured_calls: list[dict] = []

    def with_structured_output(self, schema, **kwargs):
        self.structured_calls.append({"schema": schema, **kwargs})
        return FakeStructuredResult(self.responder)


class FakePrompt:
    def __init__(self, responder) -> None:
        self.responder = responder

    def __or__(self, other):
        # ``prompt | structured_runnable``: the runnable does the parsing, so keep it as-is.
        return other


def install_fake_analyzer(monkeypatch, responder) -> None:
    monkeypatch.setattr(
        analyzer,
        "build_analyzer_llm",
        lambda: (FakePrompt(responder), FakeStructuredLLM(responder)),
    )


def analysis_json(text: str, kind: str = "narration") -> str:
    return json.dumps(
        {
            "segments": [
                {
                    "type": kind,
                    "text": text,
                    "confidence": 0.9,
                    "reason": "测试替身",
                    "speakerName": "旁白",
                    "speakerGender": "unknown",
                    "emotion": "neutral",
                    "voiceStyle": "neutral_narrator",
                    "delivery": "自然朗读。",
                    "durationSec": 3.0,
                }
            ],
            "songCandidates": [],
        },
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------------------
# catalogue / schema
# --------------------------------------------------------------------------------------


def test_registry_exposes_exactly_seven_workflows():
    workflows = registry.list_workflows()

    assert {item["id"] for item in workflows} == EXPECTED_WORKFLOW_IDS
    assert len(workflows) == 7
    # order is what the workflow page groups on, so it must be unique
    assert len({item["order"] for item in workflows}) == 7
    assert [item["order"] for item in workflows] == sorted(item["order"] for item in workflows)


def test_registry_field_schema_matches_the_frontend_contract():
    for spec in registry.WORKFLOWS.values():
        payload = spec.public_dict()
        assert payload["name"] and payload["group"] and payload["description"]
        assert payload["fields"], f"{spec.id} must declare at least one input"
        for item in payload["fields"]:
            assert item["type"] in registry.FIELD_TYPES
            assert item["name"] and item["label"]
            if item["type"] in {"project", "chapter"}:
                assert item["name"] in {"projectId", "chapterId"}


def test_workflow_listing_endpoint_returns_the_catalogue():
    response = client.get("/api/workflows")

    assert response.status_code == 200
    body = response.json()["workflows"]
    assert {item["id"] for item in body} == EXPECTED_WORKFLOW_IDS
    content_analysis = next(item for item in body if item["id"] == "content-analysis")
    assert content_analysis["requiresLlm"] is True
    assert {field["name"] for field in content_analysis["fields"]} == {"projectId", "chunkChars"}


# --------------------------------------------------------------------------------------
# input validation
# --------------------------------------------------------------------------------------


def test_unknown_workflow_is_rejected_with_404():
    response = client.post("/api/workflows/not-a-workflow/runs", json={})

    assert response.status_code == 404
    assert "未知工作流" in response.json()["detail"]


@pytest.mark.parametrize(
    "workflow_id, payload",
    [
        ("chapter-split", {}),
        ("chapter-split", {"projectId": "   "}),
        ("chapter-split", {"projectId": "abc", "bogus": 1}),
        ("content-analysis", {"projectId": "abc", "chunkChars": "不是数字"}),
        ("timeline-edit", {"projectId": "abc", "segments": {"not": "a list"}}),
        ("timeline-edit", {"projectId": "abc", "segments": "[{", "extra": 1}),
        ("timeline-edit", {"projectId": "abc", "segments": [narration_segment()], "bgmVolume": True}),
    ],
)
def test_invalid_run_inputs_are_rejected_with_422(workflow_id, payload):
    response = client.post(f"/api/workflows/{workflow_id}/runs", json=payload)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]


def test_blank_optional_field_falls_back_to_the_node_default():
    cleaned = registry.validate_workflow_inputs("content-analysis", {"projectId": "abc", "chunkChars": "  "})

    assert cleaned == {"projectId": "abc"}


def test_defaulted_fields_are_filled_in_by_the_registry():
    cleaned = registry.validate_workflow_inputs(
        "timeline-edit",
        {"projectId": "abc", "segments": [narration_segment()]},
    )

    assert cleaned["bgmVolume"] == 0.22
    assert cleaned["narrationVolume"] == 1.0
    assert cleaned["songStartSec"] == 0.0


def test_json_string_segments_are_parsed_by_the_registry():
    cleaned = registry.validate_workflow_inputs(
        "timeline-edit",
        {"projectId": "abc", "segments": json.dumps([narration_segment()])},
    )

    assert isinstance(cleaned["segments"], list)
    assert cleaned["segments"][0]["text"] == "旁白内容。"


def test_empty_segment_list_fails_inside_the_graph_not_at_the_boundary():
    project_id = create_project("第一章 开始\n旁白内容。")

    snapshot = run_workflow("timeline-edit", {"projectId": project_id, "segments": []})

    assert snapshot["status"] == "failed"
    assert snapshot["failedNode"] == "validate_timeline"
    assert "时间轴片段不能为空" in snapshot["error"]


# --------------------------------------------------------------------------------------
# 1. project-intake
# --------------------------------------------------------------------------------------


def test_project_intake_graph_creates_a_project_and_previews_chapters():
    state, events = invoke_graph("project-intake", {"text": "第一章 登台\n内容一。\n第二章 离开\n内容二。"})

    assert node_names(state) == ["validate_text", "create_project", "preview_chapters"]
    assert state["result"]["chapterCount"] == 2
    assert state["result"]["chapters"][0]["title"] == "第一章 登台"
    assert state["result"]["projectPath"].startswith("/projects/")
    assert [event["type"] for event in events if event["type"] == "node_started"] == [
        "node_started",
        "node_started",
        "node_started",
    ]
    assert project_store.get_project(state["result"]["id"]).text.startswith("第一章")


def test_project_intake_rejects_empty_text():
    with pytest.raises(WorkflowError) as exc:
        invoke_graph("project-intake", {"text": "   \n  "})

    assert "不能为空" in exc.value.message
    assert exc.value.code == "empty_text"


def test_project_intake_run_reports_a_readable_boundary_error():
    response = client.post("/api/workflows/project-intake/runs", json={"text": "   "})

    assert response.status_code == 422
    assert "缺少必填参数：文本内容" in response.json()["detail"]


# --------------------------------------------------------------------------------------
# 2. chapter-split
# --------------------------------------------------------------------------------------


def test_chapter_split_graph_summarises_every_chapter():
    project_id = create_project("第一章 登台\n内容一。\n第二章 离开\n内容二更长一些。")

    snapshot = run_workflow("chapter-split", {"projectId": project_id})

    assert snapshot["status"] == "succeeded"
    assert node_names(snapshot) == ["load_project", "split_chapters", "summarize"]
    assert snapshot["result"]["chapterCount"] == 2
    assert snapshot["result"]["longestChapter"] > 0


def test_chapter_split_warns_when_no_heading_is_found():
    project_id = create_project("这只是普通正文，没有章节标题。")

    snapshot = run_workflow("chapter-split", {"projectId": project_id})

    assert snapshot["status"] == "succeeded"
    assert snapshot["result"]["chapterCount"] == 1
    assert any("未识别到章节标题" in warning for warning in snapshot["warnings"])


def test_missing_project_fails_on_the_load_node():
    snapshot = run_workflow("chapter-split", {"projectId": "deadbeefdead"})

    assert snapshot["status"] == "failed"
    assert snapshot["failedNode"] == "load_project"


# --------------------------------------------------------------------------------------
# 3. content-analysis (LLM + repair loop)
# --------------------------------------------------------------------------------------


def test_content_analysis_persists_analysis_and_timeline(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    install_fake_analyzer(monkeypatch, lambda text: analysis_json("旁白内容。"))

    snapshot = run_workflow("content-analysis", {"projectId": project_id})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert node_names(snapshot) == [
        "load_project",
        "split_chapters",
        "build_chunks",
        "llm_analyze",
        "validate_json",
        "merge_analysis",
        "persist_analysis",
    ]
    assert snapshot["result"]["segmentCount"] >= 1

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["hasAnalysis"] is True
    assert detail["hasTimeline"] is True
    assert detail["analysis"]["analysisEngine"] == "langchain"


def test_content_analysis_repairs_a_chunk_whose_json_does_not_parse(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    calls: list[str] = []

    def responder(text: str) -> str:
        calls.append(text)
        if len(calls) == 1:
            return "这不是 JSON，只是随便一段话。"
        return analysis_json("旁白内容。")

    install_fake_analyzer(monkeypatch, responder)

    snapshot = run_workflow("content-analysis", {"projectId": project_id})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    names = node_names(snapshot)
    assert names.count("repair_json") == 1
    assert names.count("validate_json") == 2
    assert "上一次的输出无法解析为合法 JSON" in calls[1]


def test_content_analysis_repairs_only_the_chunk_that_failed(monkeypatch):
    project_id = create_project("第一章 登台\n" + "旁白内容。" * 20 + "\n\n" + "另一段旁白。" * 20)
    calls: list[str] = []

    def responder(text: str) -> str:
        calls.append(text)
        if "另一段旁白" in text and "上一次的输出无法解析" not in text:
            return "这不是 JSON，只是随便一段话。"
        return analysis_json("另一段旁白。" if "另一段旁白" in text else "旁白内容。")

    install_fake_analyzer(monkeypatch, responder)

    snapshot = run_workflow("content-analysis", {"projectId": project_id, "chunkChars": 200})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert node_names(snapshot).count("repair_json") == 1
    assert len(calls) == 3
    # The successfully parsed chunk must not be sent to the model a second time.
    assert len([call for call in calls if "旁白内容。" in call]) == 1
    assert "上一次的输出无法解析" in calls[2]


def test_content_analysis_gives_up_after_max_attempts(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    install_fake_analyzer(monkeypatch, lambda text: "永远不是 JSON")

    snapshot = run_workflow("content-analysis", {"projectId": project_id})

    assert snapshot["status"] == "failed"
    assert snapshot["errorCode"] == "llm_json_invalid"
    assert snapshot["failedNode"] == "give_up"
    assert node_names(snapshot).count("repair_json") == analysis_nodes.MAX_LLM_ATTEMPTS - 1


def test_content_analysis_reports_a_missing_api_key_as_a_node_failure(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    snapshot = run_workflow("content-analysis", {"projectId": project_id})

    assert snapshot["status"] == "failed"
    assert snapshot["failedNode"] == "llm_analyze"
    assert "OPENAI_API_KEY" in snapshot["error"]


def test_chunk_chars_override_is_validated_inside_the_graph(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    install_fake_analyzer(monkeypatch, lambda text: analysis_json("旁白内容。"))

    snapshot = run_workflow("content-analysis", {"projectId": project_id, "chunkChars": 100})

    assert snapshot["status"] == "failed"
    assert snapshot["failedNode"] == "build_chunks"
    assert "chunkChars" in snapshot["error"]


# --------------------------------------------------------------------------------------
# 4. chapter-analysis
# --------------------------------------------------------------------------------------


def test_chapter_analysis_merges_one_chapter_into_the_timeline(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。\n第二章 离开\n另一段旁白。")
    install_fake_analyzer(monkeypatch, lambda text: analysis_json("旁白内容。"))

    snapshot = run_workflow("chapter-analysis", {"projectId": project_id, "chapterId": "chap-001"})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert snapshot["result"]["chapterId"] == "chap-001"
    assert snapshot["result"]["chapterSegmentCount"] >= 1
    assert "skip_lrc" in node_names(snapshot)


def test_chapter_analysis_applies_the_chapter_lrc(monkeypatch):
    project_id = create_project("第一章 登台\n一闪一闪亮晶晶\n第二章 离开\n旁白。")
    install_fake_analyzer(monkeypatch, lambda text: analysis_json("一闪一闪亮晶晶", kind="lyric"))
    upload = client.post(
        f"/api/projects/{project_id}/chapters/chap-001/lyric-file",
        files={"file": ("lyrics.lrc", "[00:12.00]一闪一闪亮晶晶\n".encode("utf-8"), "text/plain")},
    )
    assert upload.status_code == 200, upload.text

    snapshot = run_workflow("chapter-analysis", {"projectId": project_id, "chapterId": "chap-001"})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    names = node_names(snapshot)
    assert "apply_lrc" in names
    assert "skip_lrc" not in names
    assert snapshot["result"]["chapterLrcAlignedCount"] >= 1


def test_chapter_analysis_rejects_an_unknown_chapter(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    install_fake_analyzer(monkeypatch, lambda text: analysis_json("旁白内容。"))

    snapshot = run_workflow("chapter-analysis", {"projectId": project_id, "chapterId": "chap-999"})

    assert snapshot["status"] == "failed"
    assert snapshot["errorCode"] == "chapter_not_found"
    assert snapshot["failedNode"] == "resolve_chapter"


# --------------------------------------------------------------------------------------
# 5. song-alignment
# --------------------------------------------------------------------------------------


def upload_song(project_id: str) -> None:
    response = client.post(
        f"/api/projects/{project_id}/song-file",
        files={"file": ("song.mp3", b"ID3fake-mp3-bytes", "audio/mpeg")},
    )
    assert response.status_code == 200, response.text


def test_song_alignment_writes_cues_from_the_inline_lrc():
    project_id = create_project("第一章 登台\n一闪一闪亮晶晶")
    patch_timeline(project_id, [lyric_segment()])
    upload_song(project_id)

    snapshot = run_workflow(
        "song-alignment",
        {"projectId": project_id, "lrcText": "[00:12.00]一闪一闪亮晶晶\n[00:18.00]挂在天空放光明\n"},
    )

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert snapshot["result"]["lrcSource"] == "inline"
    assert snapshot["result"]["matchedCount"] == 1
    assert "parse_inline_lrc" in node_names(snapshot)

    timeline = client.get(f"/api/projects/{project_id}").json()["timeline"]
    assert timeline["segments"][0]["songClipStartSec"] == 12.0
    assert timeline["segments"][0]["lyricMatch"]["source"] == "chapter-lrc"


def test_song_alignment_uses_the_chapter_lrc_file():
    project_id = create_project("第一章 登台\n一闪一闪亮晶晶")
    patch_timeline(project_id, [lyric_segment()])
    upload_song(project_id)
    client.post(
        f"/api/projects/{project_id}/chapters/chap-001/lyric-file",
        files={"file": ("lyrics.lrc", "[00:05.00]一闪一闪亮晶晶\n".encode("utf-8"), "text/plain")},
    )

    snapshot = run_workflow("song-alignment", {"projectId": project_id, "chapterId": "chap-001"})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert snapshot["result"]["lrcSource"] == "chapter"
    assert "parse_chapter_lrc" in node_names(snapshot)


def test_song_alignment_skips_when_no_lrc_is_available():
    project_id = create_project("第一章 登台\n一闪一闪亮晶晶")
    patch_timeline(project_id, [lyric_segment()])
    upload_song(project_id)

    snapshot = run_workflow("song-alignment", {"projectId": project_id})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert snapshot["result"]["lrcSource"] == "none"
    assert snapshot["result"]["matchedCount"] == 0
    assert any("未提供 LRC" in warning for warning in snapshot["warnings"])
    skipped = [step for step in snapshot["steps"] if step["node"] == "skip_lrc"]
    assert skipped and skipped[0]["status"] == "skipped"


def test_song_alignment_requires_an_uploaded_song():
    project_id = create_project("第一章 登台\n一闪一闪亮晶晶")
    patch_timeline(project_id, [lyric_segment()])

    snapshot = run_workflow("song-alignment", {"projectId": project_id, "lrcText": "[00:12.00]一闪一闪亮晶晶\n"})

    assert snapshot["status"] == "failed"
    assert snapshot["errorCode"] == "missing_song"
    assert snapshot["failedNode"] == "validate_assets"
    assert "MP3" in snapshot["error"]


def test_song_alignment_flags_low_confidence_matches_for_review():
    project_id = create_project("第一章 登台\n一闪一闪亮晶晶")
    patch_timeline(project_id, [lyric_segment("一闪一闪亮晶晶")])
    upload_song(project_id)

    # SequenceMatcher ratio for this pair is ~0.71: above the 0.45 match floor, below the 0.75
    # review threshold, so the segment is cued *and* listed for human confirmation.
    snapshot = run_workflow(
        "song-alignment",
        {"projectId": project_id, "lrcText": "[00:12.00]一闪一闪亮金金\n"},
    )

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert snapshot["result"]["matchedCount"] == 1
    assert snapshot["result"]["needsReviewCount"] == 1
    assert snapshot["result"]["needsReview"][0]["line"] == "一闪一闪亮金金"
    assert 0.45 <= snapshot["result"]["needsReview"][0]["confidence"] < 0.75


# --------------------------------------------------------------------------------------
# 6. timeline-edit
# --------------------------------------------------------------------------------------


def test_timeline_edit_persists_normalised_segments():
    project_id = create_project("第一章 登台\n旁白内容。")

    snapshot = run_workflow(
        "timeline-edit",
        {
            "projectId": project_id,
            "segments": [narration_segment(duration=12.345), {**narration_segment("第二段。"), "id": "seg-002"}],
            "bgmVolume": 5,
            "narrationVolume": -1,
            "songStartSec": 3.5,
        },
    )

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert snapshot["result"]["segmentCount"] == 2
    # save_timeline clamps volume settings, exactly like the existing PATCH endpoint
    assert snapshot["result"]["bgmVolume"] == 1.0
    assert snapshot["result"]["narrationVolume"] == 0.0
    assert snapshot["result"]["songStartSec"] == 3.5

    timeline = client.get(f"/api/projects/{project_id}").json()["timeline"]
    assert timeline["segments"][0]["durationSec"] == 12.35


def test_timeline_edit_rejects_a_segment_without_text():
    project_id = create_project("第一章 登台\n旁白内容。")

    snapshot = run_workflow(
        "timeline-edit",
        {"projectId": project_id, "segments": [{"id": "seg-001", "type": "narration", "text": "  "}]},
    )

    assert snapshot["status"] == "failed"
    assert snapshot["errorCode"] == "invalid_timeline"
    assert "缺少 text" in snapshot["error"]


# --------------------------------------------------------------------------------------
# 7. render
# --------------------------------------------------------------------------------------


def fake_narration_writer(_timeline, _chunks_dir, output_path, include_lyrics):  # noqa: ARG001
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"RIFFfakeWAVE")


def fake_failing_narration(*_args, **_kwargs):
    raise HTTPException(status_code=502, detail="AI TTS failed: boom")


def test_render_exports_narration_when_there_is_no_song(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    patch_timeline(project_id, [narration_segment()])
    monkeypatch.setattr(render, "build_narration_track", fake_narration_writer)

    snapshot = run_workflow("render", {"projectId": project_id})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert snapshot["result"]["status"] == "narration-only"
    assert snapshot["result"]["outputUrl"].startswith(f"/outputs/{project_id}/")
    assert "export_narration" in node_names(snapshot)
    renders = client.get(f"/api/projects/{project_id}/renders").json()["renders"]
    assert renders[0]["status"] == "succeeded"


def test_render_mixes_when_a_song_and_ffmpeg_are_available(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    patch_timeline(project_id, [narration_segment()])
    upload_song(project_id)
    monkeypatch.setattr(render, "build_narration_track", fake_narration_writer)
    monkeypatch.setattr(render, "find_ffmpeg", lambda: "/fake/ffmpeg")

    def fake_mix(_ffmpeg, _narration, _song, output_path, _timeline, lyric_song_paths=None):  # noqa: ARG001
        output_path.write_bytes(b"ID3fake-mixed")

    monkeypatch.setattr(render, "mix_with_song", fake_mix)

    snapshot = run_workflow("render", {"projectId": project_id})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert snapshot["result"]["status"] == "mixed"
    assert "mix_with_song" in node_names(snapshot)


def test_render_warns_and_exports_narration_without_ffmpeg(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    patch_timeline(project_id, [narration_segment()])
    upload_song(project_id)
    monkeypatch.setattr(render, "build_narration_track", fake_narration_writer)
    monkeypatch.setattr(render, "find_ffmpeg", lambda: None)

    snapshot = run_workflow("render", {"projectId": project_id})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    assert snapshot["result"]["status"] == "narration-only"
    assert any("ffmpeg" in warning for warning in snapshot["warnings"])


def test_render_requires_a_song_source_for_lyric_segments(monkeypatch):
    project_id = create_project("第一章 登台\n一闪一闪亮晶晶")
    patch_timeline(project_id, [lyric_segment()])
    monkeypatch.setattr(render, "find_ffmpeg", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(render, "build_narration_track", fake_narration_writer)

    snapshot = run_workflow("render", {"projectId": project_id})

    assert snapshot["status"] == "failed"
    assert snapshot["failedNode"] == "resolve_mix_source"
    assert "歌词片段" in snapshot["error"]


def test_render_failure_marks_the_render_record_as_failed(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    patch_timeline(project_id, [narration_segment()])
    cleanup_calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(render, "build_narration_track", fake_failing_narration)
    monkeypatch.setattr(
        render,
        "fail_render_record",
        lambda pid, render_id, error: cleanup_calls.append((pid, render_id, error)),
    )

    snapshot = run_workflow("render", {"projectId": project_id})

    assert snapshot["status"] == "failed"
    assert snapshot["failedNode"] == "build_narration"
    assert "boom" in snapshot["error"]
    assert len(cleanup_calls) == 1
    cleaned_project, cleaned_render, cleaned_error = cleanup_calls[0]
    assert cleaned_project == project_id
    assert cleaned_render
    assert "boom" in cleaned_error
    prepare = next(step for step in snapshot["steps"] if step["node"] == "prepare_render")
    assert cleaned_render in prepare["detail"]


def test_a_failed_render_attempt_leaves_no_stale_render_record(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    patch_timeline(project_id, [narration_segment()])
    monkeypatch.setattr(render, "build_narration_track", fake_failing_narration)
    # The real cleanup hook calls fail_render_record, which prunes failed attempts: without the
    # hook the "running" row would linger forever because pruning never touches running renders.
    monkeypatch.setattr(render, "find_ffmpeg", lambda: None)

    snapshot = run_workflow("render", {"projectId": project_id})

    assert snapshot["status"] == "failed"
    assert client.get(f"/api/projects/{project_id}/renders").json()["renders"] == []


def test_render_without_an_analysis_fails_with_a_readable_message(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    monkeypatch.setattr(render, "build_narration_track", fake_narration_writer)

    snapshot = run_workflow("render", {"projectId": project_id})

    assert snapshot["status"] == "failed"
    assert snapshot["failedNode"] == "load_timeline"
    assert "请先运行" in snapshot["error"]
    # prepare_render already created a record; the cleanup hook has to clear it
    assert client.get(f"/api/projects/{project_id}/renders").json()["renders"] == []


# --------------------------------------------------------------------------------------
# event stream, persistence, registry internals
# --------------------------------------------------------------------------------------


def collect_sse(run_id: str, headers: dict | None = None) -> tuple[str, list[dict]]:
    with client.stream("GET", f"/api/workflows/runs/{run_id}/events", headers=headers or {}) as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        body = "".join(stream.iter_text())
    return body, parse_sse(body)


def parse_sse(body: str) -> list[dict]:
    events = []
    for frame in body.split("\n\n"):
        data_line = next((line for line in frame.splitlines() if line.startswith("data: ")), None)
        if not data_line:
            continue
        events.append(json.loads(data_line.removeprefix("data: ")))
    return events


def test_sse_replay_returns_every_event_and_ends_the_stream():
    project_id = create_project("第一章 登台\n内容一。")
    snapshot = run_workflow("chapter-split", {"projectId": project_id})

    body, events = collect_sse(snapshot["runId"])

    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_finished"
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert body.endswith("\n\n")


def test_last_event_id_replays_only_newer_events():
    project_id = create_project("第一章 登台\n内容一。")
    snapshot = run_workflow("chapter-split", {"projectId": project_id})
    _, all_events = collect_sse(snapshot["runId"])

    _, replay = collect_sse(snapshot["runId"], headers={"Last-Event-ID": "3"})

    assert [event["seq"] for event in replay] == [event["seq"] for event in all_events if event["seq"] > 3]
    assert replay[0]["seq"] == 4


def test_sse_for_a_missing_run_returns_404():
    assert client.get("/api/workflows/runs/does-not-exist/events").status_code == 404


def test_run_snapshot_is_available_over_http_with_its_events():
    project_id = create_project("第一章 登台\n内容一。")
    snapshot = run_workflow("chapter-split", {"projectId": project_id})

    response = client.get(f"/api/workflows/runs/{snapshot['runId']}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["events"]
    assert body["lastSeq"] == body["events"][-1]["seq"]


def test_runs_are_listed_newest_first():
    project_id = create_project("第一章 登台\n内容一。")
    first = run_workflow("chapter-split", {"projectId": project_id})
    second = run_workflow("chapter-split", {"projectId": project_id})

    listed = client.get("/api/workflows/runs").json()["runs"]
    ids = [item["runId"] for item in listed]

    assert first["runId"] in ids and second["runId"] in ids


def test_a_finished_run_survives_a_registry_reload():
    project_id = create_project("第一章 登台\n内容一。")
    snapshot = run_workflow("chapter-split", {"projectId": project_id})
    path = runner.RUNS_DIR / f"{snapshot['runId']}.json"
    assert path.exists()

    run_registry.reset_for_tests()
    reloaded = runner.run_registry.get(snapshot["runId"])

    assert reloaded is not None
    assert reloaded.status == "succeeded"
    assert reloaded.result["chapterCount"] == 1
    assert [event["type"] for event in reloaded.events][-1] == "run_finished"


def test_an_interrupted_run_is_marked_failed_when_reloaded():
    interrupted = runner.RunRecord(id="interruptedtest", workflow_id="chapter-split", inputs={"projectId": "x"})
    interrupted.status = "running"
    interrupted.seq = 1
    interrupted.events = [
        {"seq": 1, "runId": interrupted.id, "workflowId": "chapter-split", "type": "run_started", "at": "2026-01-01T00:00:00+00:00"}
    ]
    runner.persist_run(interrupted)

    run_registry.reset_for_tests()
    reloaded = run_registry.get("interruptedtest")

    assert reloaded is not None
    assert reloaded.status == "interrupted"
    assert reloaded.errorCode == "interrupted"
    assert reloaded.events[-1]["type"] == "run_failed"
    (runner.RUNS_DIR / "interruptedtest.json").unlink(missing_ok=True)


def test_prune_runs_keeps_the_newest_files():
    for index in range(3):
        record = runner.RunRecord(id=f"prunetest{index}", workflow_id="chapter-split", inputs={})
        record.status = "succeeded"
        runner.persist_run(record)
        time.sleep(0.01)

    removed = runner.prune_runs(keep=2)

    # Only the two newest run files survive, so the oldest of this trio is pruned and the other
    # two stay even though earlier tests wrote runs in between.
    assert "prunetest0" in removed
    assert "prunetest1" not in removed
    assert "prunetest2" not in removed
    assert (runner.RUNS_DIR / "prunetest1.json").exists()
    assert (runner.RUNS_DIR / "prunetest2.json").exists()
    for name in removed:
        (runner.RUNS_DIR / f"{name}.json").unlink(missing_ok=True)


def test_merge_state_update_appends_list_channels_and_overwrites_the_rest():
    merged = merge_state_update(
        {"steps": [{"node": "a"}], "warnings": ["one"], "data": {"x": 1}},
        {"steps": [{"node": "b"}], "warnings": ["two"], "data": {"x": 2}, "result": {"ok": True}},
    )

    assert [step["node"] for step in merged["steps"]] == ["a", "b"]
    assert merged["warnings"] == ["one", "two"]
    assert merged["data"] == {"x": 2}
    assert merged["result"] == {"ok": True}


def test_event_sequence_is_monotonic_and_broadcast_to_subscribers():
    record = runner.RunRecord(id="seqtest", workflow_id="chapter-split", inputs={})
    record.emit("run_started")
    record.emit("node_started", node="a")

    assert [event["seq"] for event in record.events] == [1, 2]
    assert record.snapshot()["lastSeq"] == 2
    runner.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (runner.RUNS_DIR / f"{record.id}.json").unlink(missing_ok=True)


def test_run_workflow_directory_stays_inside_the_isolated_data_dir():
    assert runner.RUNS_DIR == project_store.DATA_DIR / "workflow_runs"


def test_render_cleanup_ignores_a_state_without_a_render_id():
    # The runner calls the cleanup hook even when prepare_render never succeeded.
    render.cleanup_failed_render({"data": {}}, WorkflowError("boom"))


def test_step_node_records_skipped_status():
    events: list[dict] = []

    def skipping_node(_state):
        return {"__status__": "skipped", "__detail__": "nothing to do", "warnings": ["注意"]}

    graph = StateGraph(WorkflowState)
    graph.add_node("skip", step_node("test-skip", "skip", "跳过", skipping_node))
    graph.add_edge(START, "skip")
    graph.add_edge("skip", END)
    compiled = graph.compile()

    final = compiled.invoke(
        {"data": {}, "steps": [], "warnings": []},
        config={"configurable": {"emit": lambda event_type, **fields: events.append({"type": event_type, **fields})}},
    )

    assert final["steps"][0]["status"] == "skipped"
    assert final["warnings"] == ["注意"]
    assert "__status__" not in final["steps"][0]


def test_extra_runs_stay_queued_while_the_concurrency_limit_is_full(monkeypatch):
    released = {"value": False}

    def blocking_body(_state):
        deadline = time.time() + 5
        while not released["value"] and time.time() < deadline:
            time.sleep(0.01)
        return {"result": {"ok": True}}

    graph = StateGraph(WorkflowState)
    graph.add_node("blocking", step_node("test-block", "blocking", "阻塞", blocking_body))
    graph.add_edge(START, "blocking")
    graph.add_edge("blocking", END)
    compiled = graph.compile()

    monkeypatch.setitem(registry._COMPILED_GRAPHS, "blocking-workflow", compiled)
    monkeypatch.setitem(
        registry.WORKFLOWS,
        "blocking-workflow",
        registry.WorkflowSpec(
            id="blocking-workflow",
            name="阻塞测试",
            group="测试",
            order=99,
            description="test double",
            fields=(),
            graph_factory=lambda: compiled,
        ),
    )
    monkeypatch.setattr(runner.run_registry, "_semaphore", threading.Semaphore(1))

    first = run_registry.create_run("blocking-workflow", {})
    second = run_registry.create_run("blocking-workflow", {})
    time.sleep(0.15)
    assert run_registry.get(first.id).status == "running"
    assert run_registry.get(second.id).status == "queued"

    released["value"] = True
    wait_for_run(first.id)
    wait_for_run(second.id)
    assert run_registry.get(second.id).status == "succeeded"


def test_live_sse_stream_delivers_events_from_a_running_workflow(monkeypatch):
    def slow_body(_state):
        time.sleep(0.35)
        return {"result": {"ok": True}}

    graph = StateGraph(WorkflowState)
    graph.add_node("slow", step_node("live-sse", "slow", "慢节点", slow_body))
    graph.add_edge(START, "slow")
    graph.add_edge("slow", END)
    compiled = graph.compile()

    monkeypatch.setitem(registry._COMPILED_GRAPHS, "live-sse", compiled)
    monkeypatch.setitem(
        registry.WORKFLOWS,
        "live-sse",
        registry.WorkflowSpec(
            id="live-sse",
            name="实时测试",
            group="测试",
            order=100,
            description="test double",
            fields=(),
            graph_factory=lambda: compiled,
        ),
    )

    record = run_registry.create_run("live-sse", {})
    time.sleep(0.05)
    assert run_registry.get(record.id).status == "running"

    _, events = collect_sse(record.id)

    assert events[0]["type"] == "run_started"
    assert any(event["type"] == "node_started" and event.get("node") == "slow" for event in events)
    assert any(event["type"] == "node_finished" and event.get("node") == "slow" for event in events)
    assert events[-1]["type"] == "run_finished"


def test_each_render_attempt_gets_its_own_output_directory(monkeypatch):
    project_id = create_project("第一章 登台\n旁白内容。")
    patch_timeline(project_id, [narration_segment()])
    monkeypatch.setattr(render, "build_narration_track", fake_narration_writer)

    snapshot = run_workflow("render", {"projectId": project_id})

    assert snapshot["status"] == "succeeded", snapshot.get("error")
    render_id = snapshot["result"]["renderId"]
    assert render_id
    assert snapshot["result"]["outputUrl"] == f"/outputs/{project_id}/{render_id}/narration.wav"
    assert (project_store.OUTPUTS_DIR / project_id / render_id / "narration.wav").exists()
