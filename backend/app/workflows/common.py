from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException

from ..services.chapter_service import TextChapter, normalize_text, split_text_into_chapters
from ..services.project_store import Project, get_project, read_json, read_stored_timeline

DETAIL_KEY = "__detail__"
STATUS_KEY = "__status__"

NodeFn = Callable[[dict], "dict | None"]


class WorkflowError(RuntimeError):
    """A workflow failed for a reason the user can act on.

    Node wrappers translate service-level ``HTTPException`` and unexpected exceptions into
    this type, so the runner has a single place to build the ``run_failed`` event.
    """

    def __init__(self, message: str, *, code: str = "workflow_failed", node: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.node = node


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _noop_emit(*_args: Any, **_kwargs: Any) -> None:
    return None


def get_emitter(config: Any) -> Callable[..., None]:
    """Read the run-scoped event emitter that the runner passes through the graph config."""

    if not isinstance(config, dict):
        return _noop_emit
    configurable = config.get("configurable") or {}
    if not isinstance(configurable, dict):
        return _noop_emit
    emit = configurable.get("emit")
    return emit if callable(emit) else _noop_emit


def elapsed_millis(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _http_error_message(exc: HTTPException) -> str:
    detail = exc.detail
    return detail if isinstance(detail, str) else str(detail)


def step_node(workflow_id: str, node: str, label: str, fn: NodeFn):
    """Wrap a plain node function with step bookkeeping and event emission.

    The returned callable is what gets registered on the ``StateGraph``: it emits
    ``node_started`` before delegating and ``node_finished`` / ``node_failed`` after, and it
    appends the ``StepRecord`` that the workflow page renders.

    A node can influence its own record by returning ``__detail__`` (summary string) and
    ``__status__`` (``succeeded`` or ``skipped``); both keys are removed before the delta is
    merged into the graph state.
    """

    def wrapper(state, config=None):
        emit = get_emitter(config)
        started_at = now_iso()
        started = time.perf_counter()
        emit("node_started", node=node, label=label, status="running")

        try:
            delta = fn(state)
        except WorkflowError as exc:
            exc.node = exc.node or node
            emit(
                "node_failed",
                node=node,
                label=label,
                status="failed",
                detail=str(exc),
                elapsedMs=elapsed_millis(started),
            )
            raise
        except HTTPException as exc:
            message = _http_error_message(exc)
            emit(
                "node_failed",
                node=node,
                label=label,
                status="failed",
                detail=message,
                elapsedMs=elapsed_millis(started),
            )
            raise WorkflowError(message, node=node) from exc
        except Exception as exc:  # noqa: BLE001 - every node failure must become a run event
            message = f"{label}失败：{exc}"
            emit(
                "node_failed",
                node=node,
                label=label,
                status="failed",
                detail=message,
                elapsedMs=elapsed_millis(started),
            )
            raise WorkflowError(message, node=node) from exc

        if not isinstance(delta, dict):
            delta = {}

        detail = str(delta.pop(DETAIL_KEY, "") or "")
        status = str(delta.pop(STATUS_KEY, "succeeded") or "succeeded")
        elapsed_ms = elapsed_millis(started)
        record = {
            "node": node,
            "label": label,
            "status": status,
            "startedAt": started_at,
            "finishedAt": now_iso(),
            "detail": detail,
            "elapsedMs": elapsed_ms,
        }
        emit(
            "node_finished",
            node=node,
            label=label,
            status=status,
            detail=detail,
            elapsedMs=elapsed_ms,
        )
        return {**delta, "steps": [record]}

    wrapper.__name__ = f"{workflow_id}_{node}"
    return wrapper


def merge_data(state, **updates: Any) -> dict:
    """Return a ``data`` channel update that keeps everything already in state."""

    current = state.get("data") or {}
    return {"data": {**current, **updates}}


def require_input(state, name: str, label: str | None = None):
    value = (state.get("inputs") or {}).get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise WorkflowError(f"缺少必填参数：{label or name}。", code="invalid_input")
    return value


def require_project(state) -> Project:
    project_id = str((state.get("inputs") or {}).get("projectId") or state.get("projectId") or "").strip()
    if not project_id:
        raise WorkflowError("缺少必填参数：projectId。", code="invalid_input")
    return get_project(project_id)


def load_chapters(project: Project) -> list[TextChapter]:
    chapters = split_text_into_chapters(project.text)
    if not chapters:
        raise WorkflowError("项目文本为空，无法切分章节。", code="empty_text")
    return chapters


def chapter_preview(chapter: TextChapter, limit: int = 60) -> str:
    compact = " ".join(normalize_text(chapter.text).split())
    return compact[:limit] + ("…" if len(compact) > limit else "")


def project_state_delta(state) -> dict:
    """Shared ``load_project`` node body: resolve the project and fail early with a clear message."""

    project = require_project(state)
    return {
        **merge_data(state, project=project, text=project.text),
        "projectId": project.id,
        "__detail__": f"读取项目 {project.id}（{len(project.text)} 字）",
    }


def split_chapters_delta(state) -> dict:
    project = state["data"]["project"]
    chapters = load_chapters(project)
    warnings = []
    if len(chapters) == 1 and chapters[0].title == "正文":
        warnings.append("未识别到章节标题，已按单章处理。")
    return {
        **merge_data(state, chapters=chapters),
        "warnings": warnings,
        "__detail__": f"切分出 {len(chapters)} 章",
    }


def load_project_analysis(project: Project, chapters: list[TextChapter]) -> dict:
    """Load the analysis a workflow should operate on.

    ``analysis.json`` provides the chapter list and song candidates, but the segments are taken
    from the live ``timeline.json`` when it exists: the editor owns the segment list, and an
    alignment workflow must not resurrect stale segments over the user's saved timeline.

    A project where the user only saved a timeline falls back to that timeline entirely.
    """

    timeline = read_stored_timeline(project)
    analysis_path = project.root / "analysis.json"
    if analysis_path.exists():
        analysis = read_json(analysis_path)
        if timeline is not None and (timeline.get("segments") or []):
            return {**analysis, "segments": timeline.get("segments") or [], "timeline": timeline}
        return analysis

    if timeline is not None:
        return {
            "analysisEngine": "timeline-only",
            "chapters": [chapter.public_dict() for chapter in chapters],
            "segments": timeline.get("segments") or [],
            "songCandidates": [],
            "timeline": timeline,
        }

    raise WorkflowError("项目还没有分析结果，请先运行「全篇内容分析」。", code="missing_analysis")


def public_chapters(chapters: list[TextChapter]) -> list[dict]:
    return [{**chapter.public_dict(), "preview": chapter_preview(chapter)} for chapter in chapters]
