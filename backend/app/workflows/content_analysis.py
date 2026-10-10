"""全篇内容分析 workflow。

This is the agentic one: it asks the configured LangChain analyzer for JSON, validates it, and
routes back into a repair node when a chunk does not parse, up to ``MAX_LLM_ATTEMPTS``.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..services.project_store import save_analysis
from . import analysis_nodes
from .common import project_state_delta, public_chapters, split_chapters_delta, step_node
from .state import WorkflowState

WORKFLOW_ID = "content-analysis"


def node_persist_analysis(state) -> dict:
    project = state["data"]["project"]
    saved = save_analysis(project.id, state["data"]["analysis"], scope="all")
    segments = saved.get("segments") or []
    lyric_count = len([segment for segment in segments if segment.get("type") == "lyric"])
    candidates = saved.get("songCandidates") or []
    result = {
        "projectId": project.id,
        "analysisId": saved.get("id"),
        "analysisEngine": saved.get("analysisEngine"),
        "chapterCount": len(saved.get("chapters") or []),
        "segmentCount": len(segments),
        "lyricCount": lyric_count,
        "songCandidateCount": len(candidates),
        "chapters": public_chapters(state["data"]["chapters"]),
        "songCandidates": candidates[:5],
        "analysisPath": f"/projects/{project.id}/analysis",
    }
    return {
        "result": result,
        "__detail__": f"已保存分析：{len(segments)} 段（歌词 {lyric_count} 段），{len(candidates)} 个歌曲候选",
    }


def build_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("load_project", step_node(WORKFLOW_ID, "load_project", "读取项目", project_state_delta))
    graph.add_node("split_chapters", step_node(WORKFLOW_ID, "split_chapters", "切分章节", split_chapters_delta))
    graph.add_node("build_chunks", step_node(WORKFLOW_ID, "build_chunks", "构建 LLM 输入块", analysis_nodes.node_build_chunks))
    graph.add_node("llm_analyze", step_node(WORKFLOW_ID, "llm_analyze", "LLM 分析", analysis_nodes.node_llm_analyze))
    graph.add_node("validate_json", step_node(WORKFLOW_ID, "validate_json", "校验 LLM JSON", analysis_nodes.node_validate_json))
    graph.add_node("repair_json", step_node(WORKFLOW_ID, "repair_json", "修复 LLM JSON", analysis_nodes.node_repair_json))
    graph.add_node("give_up", step_node(WORKFLOW_ID, "give_up", "放弃分析", analysis_nodes.node_give_up))
    graph.add_node("merge_analysis", step_node(WORKFLOW_ID, "merge_analysis", "合并片段", analysis_nodes.node_merge_analysis))
    graph.add_node("persist_analysis", step_node(WORKFLOW_ID, "persist_analysis", "保存分析结果", node_persist_analysis))

    graph.add_edge(START, "load_project")
    graph.add_edge("load_project", "split_chapters")
    graph.add_edge("split_chapters", "build_chunks")
    graph.add_edge("build_chunks", "llm_analyze")
    graph.add_edge("llm_analyze", "validate_json")
    graph.add_conditional_edges(
        "validate_json",
        analysis_nodes.route_after_validate,
        {"merge": "merge_analysis", "repair": "repair_json", "give_up": "give_up"},
    )
    graph.add_edge("repair_json", "validate_json")
    graph.add_edge("merge_analysis", "persist_analysis")
    graph.add_edge("persist_analysis", END)
    graph.add_edge("give_up", END)
    return graph.compile()
