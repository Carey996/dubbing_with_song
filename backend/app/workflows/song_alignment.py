"""歌曲/歌词对齐 workflow: 校验音源 → 解析 LRC → 匹配歌词片段 → 写入卡点 → 保存时间轴。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..services.lrc import decode_lrc_content, enrich_analysis_with_lrc, find_best_lrc_match, parse_lrc
from ..services.project_store import save_analysis
from .common import (
    WorkflowError,
    load_chapters,
    load_project_analysis,
    merge_data,
    require_project,
    step_node,
)
from .state import WorkflowState

WORKFLOW_ID = "song-alignment"
REVIEW_CONFIDENCE = 0.75


def node_validate_assets(state) -> dict:
    project = require_project(state)
    chapter_id = str((state.get("inputs") or {}).get("chapterId") or "").strip()
    lrc_text = str((state.get("inputs") or {}).get("lrcText") or "")
    chapters = load_chapters(project)

    chapter = None
    if chapter_id:
        chapter = next((item for item in chapters if item.id == chapter_id), None)
        if chapter is None:
            raise WorkflowError(f"章节不存在：{chapter_id}。", code="chapter_not_found")

    # Same source order the renderer uses: a chapter MP3 wins, the project MP3 is the fallback.
    song_path = None
    if chapter is not None and project.chapter_song_path(chapter.id).exists():
        song_path = project.chapter_song_path(chapter.id)
    elif project.song_path.exists():
        song_path = project.song_path

    if song_path is None:
        raise WorkflowError(
            "歌词/歌曲对齐前需要先上传 MP3（章节 MP3 或项目 MP3）。",
            code="missing_song",
        )

    return {
        **merge_data(
            state,
            project=project,
            chapters=chapters,
            chapter=chapter,
            lrcText=lrc_text,
            songFilename=song_path.name,
        ),
        "projectId": project.id,
        "__detail__": f"音源就绪：{song_path.name}",
    }


def route_lrc_source(state) -> str:
    data = state["data"]
    if (data.get("lrcText") or "").strip():
        return "inline"
    project = data["project"]
    chapter = data.get("chapter")
    if chapter is not None and project.chapter_lrc_path(chapter.id).exists():
        return "chapter"
    return "none"


def node_parse_inline_lrc(state) -> dict:
    lines = parse_lrc(state["data"]["lrcText"])
    return {
        **merge_data(state, lrcLines=lines, lrcSource="inline"),
        "__detail__": f"解析请求中的 LRC，共 {len(lines)} 行歌词",
    }


def node_parse_chapter_lrc(state) -> dict:
    project = state["data"]["project"]
    chapter = state["data"]["chapter"]
    lines = parse_lrc(decode_lrc_content(project.chapter_lrc_path(chapter.id).read_bytes()))
    return {
        **merge_data(state, lrcLines=lines, lrcSource="chapter"),
        "__detail__": f"解析章节 LRC，共 {len(lines)} 行歌词",
    }


def node_skip_lrc(state) -> dict:
    return {
        **merge_data(state, lrcLines=[], lrcSource="none"),
        "__status__": "skipped",
        "__detail__": "没有可用的 LRC，跳过歌词时间对齐",
        "warnings": ["未提供 LRC（也没有章节 LRC 文件），歌词片段未做时间对齐。"],
    }


def node_match_segments(state) -> dict:
    data = state["data"]
    lrc_lines = data.get("lrcLines") or []
    analysis = load_project_analysis(data["project"], data["chapters"])
    segments = analysis.get("segments") or []
    lyric_segments = [segment for segment in segments if segment.get("type") == "lyric"]

    if not lrc_lines:
        return {
            **merge_data(state, analysis=analysis, matchReport={"total": len(lyric_segments), "matched": 0, "review": []}),
            "__status__": "skipped",
            "__detail__": "没有 LRC 行可匹配，跳过歌词片段匹配",
        }

    if not lyric_segments:
        return {
            **merge_data(state, analysis=analysis, matchReport={"total": 0, "matched": 0, "review": []}),
            "__status__": "skipped",
            "__detail__": "当前时间轴没有歌词片段，无需对齐",
            "warnings": ["当前时间轴没有歌词片段，未做歌词对齐。"],
        }

    matched = 0
    review = []
    for segment in lyric_segments:
        match = find_best_lrc_match(str(segment.get("text") or ""), lrc_lines)
        if not match:
            continue
        line, score = match
        matched += 1
        if score < REVIEW_CONFIDENCE:
            review.append(
                {
                    "segmentId": segment.get("id"),
                    "text": str(segment.get("text") or "")[:40],
                    "line": line.text,
                    "confidence": round(score, 3),
                }
            )

    return {
        **merge_data(state, analysis=analysis, matchReport={"total": len(lyric_segments), "matched": matched, "review": review}),
        "__detail__": f"匹配 {matched}/{len(lyric_segments)} 个歌词片段，{len(review)} 个需要人工确认",
    }


def node_apply_cues(state) -> dict:
    lrc_lines = state["data"].get("lrcLines") or []
    if not lrc_lines:
        return {
            "__status__": "skipped",
            "__detail__": "没有 LRC 行，跳过写入卡点",
        }

    enriched = enrich_analysis_with_lrc(state["data"]["analysis"], lrc_lines)
    applied = len([segment for segment in enriched.get("segments") or [] if segment.get("lyricMatch")])
    return {
        **merge_data(state, analysis=enriched),
        "__detail__": f"写入 {applied} 个歌词片段的歌曲卡点",
    }


def node_persist_alignment(state) -> dict:
    project = state["data"]["project"]
    report = state["data"].get("matchReport") or {}
    saved = save_analysis(project.id, state["data"]["analysis"], scope="all")
    segments = saved.get("segments") or []
    review = report.get("review") or []
    result = {
        "projectId": project.id,
        "analysisId": saved.get("id"),
        "lrcSource": state["data"].get("lrcSource"),
        "lrcLineCount": len(state["data"].get("lrcLines") or []),
        "songFilename": state["data"].get("songFilename"),
        "lyricSegmentCount": report.get("total", 0),
        "matchedCount": report.get("matched", 0),
        "needsReviewCount": len(review),
        "needsReview": review[:10],
        "timelineSegmentCount": len(segments),
        "analysisPath": f"/projects/{project.id}/analysis",
    }
    return {
        "result": result,
        "__detail__": (
            f"已保存对齐结果：{result['matchedCount']}/{result['lyricSegmentCount']} 个歌词片段，"
            f"{result['needsReviewCount']} 个待确认"
        ),
    }


def build_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("validate_assets", step_node(WORKFLOW_ID, "validate_assets", "校验音源与 LRC", node_validate_assets))
    graph.add_node("parse_inline_lrc", step_node(WORKFLOW_ID, "parse_inline_lrc", "解析请求 LRC", node_parse_inline_lrc))
    graph.add_node("parse_chapter_lrc", step_node(WORKFLOW_ID, "parse_chapter_lrc", "解析章节 LRC", node_parse_chapter_lrc))
    graph.add_node("skip_lrc", step_node(WORKFLOW_ID, "skip_lrc", "跳过 LRC", node_skip_lrc))
    graph.add_node("match_segments", step_node(WORKFLOW_ID, "match_segments", "匹配歌词片段", node_match_segments))
    graph.add_node("apply_cues", step_node(WORKFLOW_ID, "apply_cues", "写入歌曲卡点", node_apply_cues))
    graph.add_node("persist_alignment", step_node(WORKFLOW_ID, "persist_alignment", "保存对齐结果", node_persist_alignment))

    graph.add_edge(START, "validate_assets")
    graph.add_conditional_edges(
        "validate_assets",
        route_lrc_source,
        {"inline": "parse_inline_lrc", "chapter": "parse_chapter_lrc", "none": "skip_lrc"},
    )
    graph.add_edge("parse_inline_lrc", "match_segments")
    graph.add_edge("parse_chapter_lrc", "match_segments")
    graph.add_edge("skip_lrc", "match_segments")
    graph.add_edge("match_segments", "apply_cues")
    graph.add_edge("apply_cues", "persist_alignment")
    graph.add_edge("persist_alignment", END)
    return graph.compile()
