"""章节切分 workflow: 读取项目 → 切分章节 → 输出概览。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .common import project_state_delta, public_chapters, split_chapters_delta, step_node
from .state import WorkflowState

WORKFLOW_ID = "chapter-split"


def node_summarize(state) -> dict:
    project = state["data"]["project"]
    chapters = state["data"]["chapters"]
    lengths = [len(chapter.text) for chapter in chapters]
    result = {
        "projectId": project.id,
        "chapterCount": len(chapters),
        "textLength": sum(lengths),
        "longestChapter": max(lengths) if lengths else 0,
        "chapters": public_chapters(chapters),
    }
    return {
        "result": result,
        "__detail__": f"输出 {len(chapters)} 章概览，最长一章 {result['longestChapter']} 字",
    }


def build_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("load_project", step_node(WORKFLOW_ID, "load_project", "读取项目", project_state_delta))
    graph.add_node("split_chapters", step_node(WORKFLOW_ID, "split_chapters", "切分章节", split_chapters_delta))
    graph.add_node("summarize", step_node(WORKFLOW_ID, "summarize", "生成章节概览", node_summarize))

    graph.add_edge(START, "load_project")
    graph.add_edge("load_project", "split_chapters")
    graph.add_edge("split_chapters", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile()
