"""时间轴编辑保存 workflow: 校验入参 → 归一化片段 → 落盘并记录。"""

from __future__ import annotations

import math

from langgraph.graph import END, START, StateGraph

from ..services.project_store import normalize_segments, save_timeline
from .common import WorkflowError, merge_data, require_project, step_node
from .state import WorkflowState

WORKFLOW_ID = "timeline-edit"

SETTING_INPUTS = ("bgmVolume", "narrationVolume", "songStartSec")


def node_validate_timeline(state) -> dict:
    inputs = state.get("inputs") or {}
    segments = inputs.get("segments")
    if not isinstance(segments, list) or not segments:
        raise WorkflowError("时间轴片段不能为空。", code="invalid_input")

    problems: list[str] = []
    for index, segment in enumerate(segments):
        position = index + 1
        if not isinstance(segment, dict):
            problems.append(f"第 {position} 段不是对象")
            continue
        if not str(segment.get("text") or "").strip():
            problems.append(f"第 {position} 段缺少 text")
        if segment.get("type") not in (None, "narration", "lyric"):
            problems.append(f"第 {position} 段的 type 不是 narration/lyric")
        for field in ("durationSec", "startSec", "songClipStartSec", "songClipEndSec"):
            if field in segment and not _is_finite_number(segment.get(field)):
                problems.append(f"第 {position} 段的 {field} 不是有效数字")

    for field in SETTING_INPUTS:
        if field in inputs and not _is_finite_number(inputs.get(field)):
            problems.append(f"{field} 不是有效数字")

    if problems:
        preview = "；".join(problems[:5])
        suffix = f"（共 {len(problems)} 处问题）" if len(problems) > 5 else ""
        raise WorkflowError(f"时间轴校验失败：{preview}{suffix}", code="invalid_timeline")

    return {
        **merge_data(state, segments=segments),
        "__detail__": f"时间轴校验通过，共 {len(segments)} 段",
    }


def _is_finite_number(value) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def node_normalize_segments(state) -> dict:
    normalized = normalize_segments(state["data"]["segments"])
    lyric_count = len([segment for segment in normalized if segment["type"] == "lyric"])
    total = sum(segment["durationSec"] for segment in normalized)
    return {
        **merge_data(state, segments=normalized),
        "__detail__": f"归一化 {len(normalized)} 段：歌词 {lyric_count} 段，总时长 {total:.1f} 秒",
    }


def node_persist_timeline(state) -> dict:
    project = require_project(state)
    inputs = state.get("inputs") or {}
    timeline = save_timeline(
        project.id,
        {
            "bgmVolume": inputs.get("bgmVolume", 0.22),
            "narrationVolume": inputs.get("narrationVolume", 1.0),
            "songStartSec": inputs.get("songStartSec", 0.0),
            "segments": state["data"]["segments"],
        },
    )
    segments = timeline.get("segments") or []
    result = {
        "projectId": project.id,
        "segmentCount": len(segments),
        "lyricCount": len([segment for segment in segments if segment.get("type") == "lyric"]),
        "totalDurationSec": round(sum(float(segment.get("durationSec") or 0) for segment in segments), 2),
        "bgmVolume": timeline.get("bgmVolume"),
        "narrationVolume": timeline.get("narrationVolume"),
        "songStartSec": timeline.get("songStartSec"),
        "analysisPath": f"/projects/{project.id}/analysis",
    }
    return {
        "result": result,
        "__detail__": f"时间轴已保存：{result['segmentCount']} 段，总时长 {result['totalDurationSec']} 秒",
    }


def build_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("validate_timeline", step_node(WORKFLOW_ID, "validate_timeline", "校验时间轴", node_validate_timeline))
    graph.add_node("normalize_segments", step_node(WORKFLOW_ID, "normalize_segments", "归一化片段", node_normalize_segments))
    graph.add_node("persist_timeline", step_node(WORKFLOW_ID, "persist_timeline", "保存时间轴", node_persist_timeline))

    graph.add_edge(START, "validate_timeline")
    graph.add_edge("validate_timeline", "normalize_segments")
    graph.add_edge("normalize_segments", "persist_timeline")
    graph.add_edge("persist_timeline", END)
    return graph.compile()
