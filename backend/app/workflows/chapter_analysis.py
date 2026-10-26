"""单章分析 workflow: 定位章节 → LLM 结构化分析（含修复回环）→ 按需叠加 LRC → 并回项目时间轴。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..services.lrc import decode_lrc_content, enrich_analysis_with_lrc, parse_lrc
from ..services.project_store import save_analysis
from . import analysis_nodes
from .common import WorkflowError, load_chapters, merge_data, require_input, require_project, step_node
from .state import WorkflowState

WORKFLOW_ID = "chapter-analysis"


def node_resolve_chapter(state) -> dict:
    project = require_project(state)
    chapter_id = str(require_input(state, "chapterId", "章节")).strip()
    project_chapters = load_chapters(project)
    chapter = next((item for item in project_chapters if item.id == chapter_id), None)
    if chapter is None:
        raise WorkflowError(f"章节不存在：{chapter_id}。", code="chapter_not_found")

    # ``chapters``/``text`` are narrowed to the target chapter so the shared analysis nodes
    # chunk and merge only this chapter; ``projectChapters`` keeps the whole book for the UI.
    return {
        **merge_data(
            state,
            project=project,
            projectChapters=project_chapters,
            chapters=[chapter],
            chapter=chapter,
            text=chapter.text,
        ),
        "projectId": project.id,
        "__detail__": f"定位章节 {chapter.id}「{chapter.title}」（{len(chapter.text)} 字）",
    }


def route_lrc_source(state) -> str:
    project = state["data"]["project"]
    chapter = state["data"]["chapter"]
    return "apply_lrc" if project.chapter_lrc_path(chapter.id).exists() else "skip_lrc"


def node_apply_lrc(state) -> dict:
    project = state["data"]["project"]
    chapter = state["data"]["chapter"]
    lrc_lines = parse_lrc(decode_lrc_content(project.chapter_lrc_path(chapter.id).read_bytes()))
    enriched = enrich_analysis_with_lrc(state["data"]["analysis"], lrc_lines)
    matched = len([segment for segment in enriched.get("segments") or [] if segment.get("lyricMatch")])
    return {
        **merge_data(state, analysis=enriched),
        "__detail__": f"用 {len(lrc_lines)} 行 LRC 对齐了 {matched} 个歌词片段",
    }


def node_skip_lrc(state) -> dict:
    return {
        "__status__": "skipped",
        "__detail__": "该章节没有 LRC 文件，跳过歌词时间对齐",
        "warnings": ["未上传该章节的 LRC 文件，歌词片段未做时间对齐。"],
    }


def node_merge_chapter(state) -> dict:
    chapters = state["data"]["projectChapters"]
    analysis = dict(state["data"]["analysis"])
    analysis["chapters"] = [chapter.public_dict() for chapter in chapters]
    segments = analysis.get("segments") or []
    return {
        **merge_data(state, analysis=analysis),
        "__detail__": f"准备把 {len(segments)} 个片段并入项目时间轴",
    }


def node_persist_chapter(state) -> dict:
    project = state["data"]["project"]
    chapter = state["data"]["chapter"]
    saved = save_analysis(
        project.id,
        state["data"]["analysis"],
        scope="chapter",
        chapter_id=chapter.id,
        chapter_title=chapter.title,
    )
    segments = saved.get("segments") or []
    chapter_segments = [segment for segment in segments if segment.get("chapterId") == chapter.id]
    lrc_aligned = len([segment for segment in chapter_segments if segment.get("lyricMatch")])
    result = {
        "projectId": project.id,
        "analysisId": saved.get("id"),
        "chapterId": chapter.id,
        "chapterTitle": chapter.title,
        "chapterSegmentCount": len(chapter_segments),
        "chapterLyricCount": len([segment for segment in chapter_segments if segment.get("type") == "lyric"]),
        "chapterLrcAlignedCount": lrc_aligned,
        "projectSegmentCount": len(segments),
        "songCandidateCount": len(saved.get("songCandidates") or []),
        "analysisPath": f"/projects/{project.id}/chapters/{chapter.id}/analysis",
    }
    return {
        "result": result,
        "__detail__": (
            f"章节「{chapter.title}」已并入时间轴：本章 {len(chapter_segments)} 段，"
            f"项目共 {len(segments)} 段"
        ),
    }


def build_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("resolve_chapter", step_node(WORKFLOW_ID, "resolve_chapter", "定位章节", node_resolve_chapter))
    graph.add_node("build_chunks", step_node(WORKFLOW_ID, "build_chunks", "构建 LLM 输入块", analysis_nodes.node_build_chunks))
    graph.add_node("llm_analyze", step_node(WORKFLOW_ID, "llm_analyze", "LLM 分析", analysis_nodes.node_llm_analyze))
    graph.add_node("validate_json", step_node(WORKFLOW_ID, "validate_json", "校验结构化结果", analysis_nodes.node_validate_json))
    graph.add_node("repair_json", step_node(WORKFLOW_ID, "repair_json", "修复结构化结果", analysis_nodes.node_repair_json))
    graph.add_node("give_up", step_node(WORKFLOW_ID, "give_up", "放弃分析", analysis_nodes.node_give_up))
    graph.add_node("merge_analysis", step_node(WORKFLOW_ID, "merge_analysis", "合并片段", analysis_nodes.node_merge_analysis))
    graph.add_node("apply_lrc", step_node(WORKFLOW_ID, "apply_lrc", "叠加 LRC 卡点", node_apply_lrc))
    graph.add_node("skip_lrc", step_node(WORKFLOW_ID, "skip_lrc", "跳过 LRC 对齐", node_skip_lrc))
    graph.add_node("merge_chapter", step_node(WORKFLOW_ID, "merge_chapter", "并入项目时间轴", node_merge_chapter))
    graph.add_node("persist_chapter", step_node(WORKFLOW_ID, "persist_chapter", "保存章节分析", node_persist_chapter))

    graph.add_edge(START, "resolve_chapter")
    graph.add_edge("resolve_chapter", "build_chunks")
    graph.add_edge("build_chunks", "llm_analyze")
    graph.add_edge("llm_analyze", "validate_json")
    graph.add_conditional_edges(
        "validate_json",
        analysis_nodes.route_after_validate,
        {"merge": "merge_analysis", "repair": "repair_json", "give_up": "give_up"},
    )
    graph.add_edge("repair_json", "validate_json")
    graph.add_conditional_edges("merge_analysis", route_lrc_source, {"apply_lrc": "apply_lrc", "skip_lrc": "skip_lrc"})
    graph.add_edge("apply_lrc", "merge_chapter")
    graph.add_edge("skip_lrc", "merge_chapter")
    graph.add_edge("merge_chapter", "persist_chapter")
    graph.add_edge("persist_chapter", END)
    graph.add_edge("give_up", END)
    return graph.compile()
