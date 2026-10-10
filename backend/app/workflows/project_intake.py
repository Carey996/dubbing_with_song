"""项目接入 workflow: 校验文本 → 创建项目 → 生成章节预览。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..services.chapter_service import normalize_text, split_text_into_chapters
from ..services.project_store import create_project
from .common import WorkflowError, merge_data, public_chapters, step_node
from .state import WorkflowState

WORKFLOW_ID = "project-intake"


def node_validate_text(state) -> dict:
    raw = (state.get("inputs") or {}).get("text")
    text = normalize_text(str(raw or ""))
    if not text:
        raise WorkflowError("文本内容不能为空。", code="empty_text")
    return {
        **merge_data(state, text=text),
        "__detail__": f"文本校验通过，共 {len(text)} 字",
    }


def node_create_project(state) -> dict:
    project = create_project(state["data"]["text"])
    return {
        **merge_data(state, project=project),
        "projectId": project.id,
        "__detail__": f"已创建项目 {project.id}",
    }


def node_preview_chapters(state) -> dict:
    project = state["data"]["project"]
    chapters = split_text_into_chapters(project.text)
    result = {
        **project.public_dict(),
        "chapterCount": len(chapters),
        "chapters": public_chapters(chapters),
        "projectPath": f"/projects/{project.id}/chapters",
    }
    return {
        "result": result,
        "__detail__": f"项目创建完成，{len(chapters)} 章可供切分与分析",
    }


def build_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("validate_text", step_node(WORKFLOW_ID, "validate_text", "校验文本", node_validate_text))
    graph.add_node("create_project", step_node(WORKFLOW_ID, "create_project", "创建项目", node_create_project))
    graph.add_node("preview_chapters", step_node(WORKFLOW_ID, "preview_chapters", "生成章节预览", node_preview_chapters))

    graph.add_edge(START, "validate_text")
    graph.add_edge("validate_text", "create_project")
    graph.add_edge("create_project", "preview_chapters")
    graph.add_edge("preview_chapters", END)
    return graph.compile()
