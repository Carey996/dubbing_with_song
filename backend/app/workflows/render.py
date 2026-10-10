"""渲染导出 workflow: 读取时间轴 → 判定混音可行性 → 生成旁白 → 混音或仅旁白 → 记录渲染结果。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..services.project_store import (
    complete_render_record,
    create_render_record,
    fail_render_record,
    read_timeline,
)
from ..services.renderer import (
    build_narration_track,
    find_ffmpeg,
    has_lyric_segments,
    mix_with_song,
    resolve_lyric_segment_song_paths,
)
from .common import WorkflowError, merge_data, now_iso, require_project, step_node
from .state import WorkflowState

WORKFLOW_ID = "render"


def node_prepare_render(state) -> dict:
    project = require_project(state)
    render_id = create_render_record(project.id)
    # render_project relies on this metadata to give every attempt its own output directory.
    project.metadata["renderId"] = render_id
    render_dir = project.output_dir
    return {
        **merge_data(state, project=project, renderId=render_id, renderDir=render_dir),
        "projectId": project.id,
        "__detail__": f"创建渲染记录 {render_id}",
    }


def node_load_timeline(state) -> dict:
    project = state["data"]["project"]
    try:
        timeline = read_timeline(project)
    except Exception as exc:  # noqa: BLE001 - convert the 400 into a named workflow failure
        raise WorkflowError(
            f"还没有可渲染的时间轴，请先运行「全篇内容分析」并保存时间轴。（{getattr(exc, 'detail', exc)}）",
            code="missing_timeline",
        ) from exc
    segments = timeline.get("segments") or []
    return {
        **merge_data(state, timeline=timeline),
        "__detail__": f"读取时间轴：{len(segments)} 段",
    }


def node_resolve_mix_source(state) -> dict:
    project = state["data"]["project"]
    timeline = state["data"]["timeline"]
    has_song = project.song_path.exists()
    has_lyrics = has_lyric_segments(timeline)
    lyric_song_paths = resolve_lyric_segment_song_paths(project, timeline) if has_lyrics else {}
    has_mix_source = has_song or bool(lyric_song_paths)
    ffmpeg = find_ffmpeg()

    if has_lyrics and not ffmpeg:
        raise WorkflowError("歌词片段需要 ffmpeg 才能从 MP3 裁切混音。", code="missing_ffmpeg")

    warnings = []
    if has_mix_source and not ffmpeg:
        warnings.append("未检测到 ffmpeg，后端无法混入 MP3，已导出旁白 WAV。")

    default_song_path = None
    if has_song:
        default_song_path = project.song_path
    elif lyric_song_paths:
        default_song_path = next(iter(lyric_song_paths.values()))

    return {
        **merge_data(
            state,
            hasSong=has_song,
            hasLyrics=has_lyrics,
            hasMixSource=has_mix_source,
            ffmpeg=ffmpeg,
            lyricSongPaths=lyric_song_paths,
            defaultSongPath=default_song_path,
        ),
        "warnings": warnings,
        "__detail__": (
            f"音源：{'项目/章节 MP3' if has_mix_source else '无'}；"
            f"歌词片段：{'有' if has_lyrics else '无'}；ffmpeg：{'可用' if ffmpeg else '不可用'}"
        ),
    }


def node_build_narration(state) -> dict:
    project = state["data"]["project"]
    timeline = state["data"]["timeline"]
    render_dir = state["data"]["renderDir"]
    chunks_dir = render_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    narration_path = render_dir / "narration.wav"
    build_narration_track(
        timeline,
        chunks_dir,
        narration_path,
        include_lyrics=not state["data"]["hasLyrics"],
    )
    return {
        **merge_data(state, narrationPath=narration_path),
        "__detail__": f"旁白音轨已生成：{narration_path.name}",
    }


def route_after_narration(state) -> str:
    data = state["data"]
    return "mix" if (data.get("hasMixSource") and data.get("ffmpeg")) else "narration_only"


def node_mix_with_song(state) -> dict:
    project = state["data"]["project"]
    output_path = state["data"]["renderDir"] / "mixed.mp3"
    mix_with_song(
        state["data"]["ffmpeg"],
        state["data"]["narrationPath"],
        state["data"]["defaultSongPath"],
        output_path,
        state["data"]["timeline"],
        lyric_song_paths=state["data"].get("lyricSongPaths") or {},
    )
    return {
        "result": {
            "status": "mixed",
            "message": "已生成旁白 + MP3 BGM 混音。",
            "outputUrl": project.output_url(output_path),
            "warnings": [],
        },
        "__detail__": f"混音完成：{output_path.name}",
    }


def node_export_narration(state) -> dict:
    project = state["data"]["project"]
    narration_path = state["data"]["narrationPath"]
    return {
        "result": {
            "status": "narration-only",
            "message": "已生成旁白 WAV。",
            "outputUrl": project.output_url(narration_path),
            "warnings": list(state.get("warnings") or []),
        },
        "__detail__": f"导出旁白 WAV：{narration_path.name}",
    }


def node_persist_render(state) -> dict:
    project = state["data"]["project"]
    render_id = state["data"]["renderId"]
    result = dict(state.get("result") or {})
    complete_render_record(project.id, render_id, result)
    merged = {
        **result,
        "projectId": project.id,
        "renderId": render_id,
        "savedAt": now_iso(),
        "analysisPath": f"/projects/{project.id}/analysis",
    }
    return {
        "result": merged,
        "__detail__": f"渲染记录 {render_id} 已保存（状态：{result.get('status')}）",
    }


def cleanup_failed_render(state, exc: Exception) -> None:
    """Mark a failed attempt instead of leaving a half-written render behind.

    The runner calls this only when the graph raised after ``prepare_render`` succeeded.
    """

    data = (state or {}).get("data") or {}
    project = data.get("project")
    render_id = data.get("renderId")
    if project is None or not render_id:
        return
    message = getattr(exc, "message", None) or str(exc)
    try:
        fail_render_record(project.id, render_id, message)
    except Exception:  # noqa: BLE001 - cleanup must never mask the original failure
        return


def build_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("prepare_render", step_node(WORKFLOW_ID, "prepare_render", "准备渲染目录", node_prepare_render))
    graph.add_node("load_timeline", step_node(WORKFLOW_ID, "load_timeline", "读取时间轴", node_load_timeline))
    graph.add_node("resolve_mix_source", step_node(WORKFLOW_ID, "resolve_mix_source", "判定混音音源", node_resolve_mix_source))
    graph.add_node("build_narration", step_node(WORKFLOW_ID, "build_narration", "生成旁白音轨", node_build_narration))
    graph.add_node("mix_with_song", step_node(WORKFLOW_ID, "mix_with_song", "混入歌曲", node_mix_with_song))
    graph.add_node("export_narration", step_node(WORKFLOW_ID, "export_narration", "导出旁白", node_export_narration))
    graph.add_node("persist_render", step_node(WORKFLOW_ID, "persist_render", "保存渲染记录", node_persist_render))

    graph.add_edge(START, "prepare_render")
    graph.add_edge("prepare_render", "load_timeline")
    graph.add_edge("load_timeline", "resolve_mix_source")
    graph.add_edge("resolve_mix_source", "build_narration")
    graph.add_conditional_edges(
        "build_narration",
        route_after_narration,
        {"mix": "mix_with_song", "narration_only": "export_narration"},
    )
    graph.add_edge("mix_with_song", "persist_render")
    graph.add_edge("export_narration", "persist_render")
    graph.add_edge("persist_render", END)
    return graph.compile()
