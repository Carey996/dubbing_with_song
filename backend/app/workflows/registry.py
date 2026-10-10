"""Workflow agent catalog: metadata, input schema, and compiled graphs.

The frontend reads ``GET /api/workflows`` and renders one form per workflow from this schema, so
the catalogue is the single source of truth for what each agent needs.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from typing import Any, Callable

from . import (
    chapter_analysis,
    chapter_split,
    content_analysis,
    project_intake,
    render,
    song_alignment,
    timeline_edit,
)
from .common import WorkflowError

FIELD_TYPES = frozenset({"text", "textarea", "number", "project", "chapter", "json"})
NUMBER_TYPES = frozenset({"number"})


@dataclass(frozen=True)
class WorkflowField:
    name: str
    label: str
    type: str
    required: bool = False
    default: Any = None
    help: str = ""
    placeholder: str = ""
    depends_on: str = ""
    expects: str = ""

    def public_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "help": self.help,
            "placeholder": self.placeholder,
            "dependsOn": self.depends_on,
            "expects": self.expects,
        }


@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    name: str
    group: str
    order: int
    description: str
    fields: tuple[WorkflowField, ...]
    graph_factory: Callable[[], Any]
    cleanup: Callable[[dict, Exception], None] | None = None
    requires_llm: bool = False
    requires_tts: bool = False
    requires_ffmpeg: bool = False

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "group": self.group,
            "order": self.order,
            "description": self.description,
            "fields": [item.public_dict() for item in self.fields],
            "requiresLlm": self.requires_llm,
            "requiresTts": self.requires_tts,
            "requiresFfmpeg": self.requires_ffmpeg,
        }


PROJECT_FIELD = WorkflowField(
    name="projectId",
    label="项目",
    type="project",
    required=True,
    help="目标项目；列表来自历史项目。",
)

CHAPTER_FIELD = WorkflowField(
    name="chapterId",
    label="章节",
    type="chapter",
    help="留空表示整篇；章节列表依赖所选项目。",
)

CHUNK_CHARS_FIELD = WorkflowField(
    name="chunkChars",
    label="每个 LLM 输入块字数",
    type="number",
    help="留空则使用服务端 ANALYZER_CHUNK_CHARS 配置（默认 1200）。",
    placeholder="1200",
)

WORKFLOWS: dict[str, WorkflowSpec] = {
    project_intake.WORKFLOW_ID: WorkflowSpec(
        id=project_intake.WORKFLOW_ID,
        name="项目接入",
        group="接入与切分",
        order=1,
        description="校验文本、创建项目，并预览章节切分结果。",
        fields=(
            WorkflowField(
                name="text",
                label="文本内容",
                type="textarea",
                required=True,
                placeholder="粘贴需要配音的正文…",
                help="粘贴正文；需要上传 txt 时请先在「项目」页处理，再在这里粘贴内容。",
            ),
        ),
        graph_factory=project_intake.build_graph,
    ),
    chapter_split.WORKFLOW_ID: WorkflowSpec(
        id=chapter_split.WORKFLOW_ID,
        name="章节切分",
        group="接入与切分",
        order=2,
        description="按中文章节标题切分项目正文，输出每章概览。",
        fields=(PROJECT_FIELD,),
        graph_factory=chapter_split.build_graph,
    ),
    content_analysis.WORKFLOW_ID: WorkflowSpec(
        id=content_analysis.WORKFLOW_ID,
        name="全篇内容分析",
        group="内容分析",
        order=3,
        description="按章节分块调用 LLM，识别旁白/歌词并给出歌曲候选；JSON 解析失败会自动重试。",
        fields=(PROJECT_FIELD, CHUNK_CHARS_FIELD),
        graph_factory=content_analysis.build_graph,
        requires_llm=True,
    ),
    chapter_analysis.WORKFLOW_ID: WorkflowSpec(
        id=chapter_analysis.WORKFLOW_ID,
        name="单章分析",
        group="内容分析",
        order=4,
        description="只分析指定章节并并回项目时间轴；若该章节有 LRC，会自动写入歌词卡点。",
        fields=(
            PROJECT_FIELD,
            WorkflowField(
                name="chapterId",
                label="章节",
                type="chapter",
                required=True,
                help="要重新分析的章节。",
            ),
            CHUNK_CHARS_FIELD,
        ),
        graph_factory=chapter_analysis.build_graph,
        requires_llm=True,
    ),
    song_alignment.WORKFLOW_ID: WorkflowSpec(
        id=song_alignment.WORKFLOW_ID,
        name="歌曲/歌词对齐",
        group="歌曲对齐",
        order=5,
        description="用 LRC 时间轴匹配歌词片段，写入歌曲片段的开始/结束卡点。",
        fields=(
            PROJECT_FIELD,
            CHAPTER_FIELD,
            WorkflowField(
                name="lrcText",
                label="LRC 内容",
                type="textarea",
                placeholder="[00:12.00] 一闪一闪亮晶晶",
                help="留空时使用所选章节已上传的 .lrc 文件；两者都没有则跳过对齐。",
            ),
        ),
        graph_factory=song_alignment.build_graph,
    ),
    timeline_edit.WORKFLOW_ID: WorkflowSpec(
        id=timeline_edit.WORKFLOW_ID,
        name="时间轴保存",
        group="时间轴与渲染",
        order=6,
        description="校验并归一化时间轴片段，写入项目的 timeline.json。",
        fields=(
            PROJECT_FIELD,
            WorkflowField(
                name="segments",
                label="时间轴片段（JSON 数组）",
                type="json",
                required=True,
                expects="list",
                help="点「载入当前时间轴」可以从项目里拉取已保存的片段再修改。",
            ),
            WorkflowField(name="bgmVolume", label="BGM 音量", type="number", default=0.22),
            WorkflowField(name="narrationVolume", label="旁白音量", type="number", default=1.0),
            WorkflowField(name="songStartSec", label="歌曲起点（秒）", type="number", default=0.0),
        ),
        graph_factory=timeline_edit.build_graph,
    ),
    render.WORKFLOW_ID: WorkflowSpec(
        id=render.WORKFLOW_ID,
        name="渲染导出",
        group="时间轴与渲染",
        order=7,
        description="按时间轴生成旁白音轨；有 MP3 和 ffmpeg 时输出混音，否则导出旁白 WAV。",
        fields=(PROJECT_FIELD,),
        graph_factory=render.build_graph,
        cleanup=render.cleanup_failed_render,
        requires_tts=True,
    ),
}

_GRAPH_LOCK = threading.Lock()
_COMPILED_GRAPHS: dict[str, Any] = {}


def list_workflows() -> list[dict]:
    return [spec.public_dict() for spec in sorted(WORKFLOWS.values(), key=lambda item: item.order)]


def get_workflow(workflow_id: str) -> WorkflowSpec:
    spec = WORKFLOWS.get(str(workflow_id or ""))
    if spec is None:
        raise WorkflowError(f"未知工作流：{workflow_id}。", code="unknown_workflow")
    return spec


def get_graph(workflow_id: str):
    spec = get_workflow(workflow_id)
    with _GRAPH_LOCK:
        graph = _COMPILED_GRAPHS.get(spec.id)
        if graph is None:
            graph = spec.graph_factory()
            _COMPILED_GRAPHS[spec.id] = graph
        return graph


def validate_workflow_inputs(workflow_id: str, inputs: Any) -> dict:
    """Validate and normalise a run request against the workflow's declared schema."""

    spec = get_workflow(workflow_id)
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise WorkflowError("请求体必须是包含参数的 JSON object。", code="invalid_input")

    known = {item.name for item in spec.fields}
    unknown = sorted(set(inputs) - known)
    if unknown:
        raise WorkflowError(f"不支持的参数：{', '.join(unknown)}。", code="invalid_input")

    cleaned: dict[str, Any] = {}
    for item in spec.fields:
        present = item.name in inputs
        raw = inputs.get(item.name)
        blank = isinstance(raw, str) and not raw.strip()
        if not present or blank:
            if item.required:
                raise WorkflowError(f"缺少必填参数：{item.label}（{item.name}）。", code="invalid_input")
            if not present and item.default is not None:
                cleaned[item.name] = item.default
            # An explicitly blank optional field is left out so the node applies its own default.
            continue
        cleaned[item.name] = _coerce_field(item, raw)

    return cleaned


def _coerce_field(item: WorkflowField, value: Any) -> Any:
    if item.type in {"text", "textarea", "project", "chapter"}:
        if not isinstance(value, str):
            raise WorkflowError(f"{item.label} 必须是文本。", code="invalid_input")
        text = value.strip()
        if item.required and not text:
            raise WorkflowError(f"缺少必填参数：{item.label}。", code="invalid_input")
        return text

    if item.type in NUMBER_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WorkflowError(f"{item.label} 必须是数字。", code="invalid_input")
        if not math.isfinite(float(value)):
            raise WorkflowError(f"{item.label} 必须是有限数字。", code="invalid_input")
        return value

    if item.type == "json":
        parsed = value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise WorkflowError(f"{item.label} 不是合法 JSON：{exc.msg}。", code="invalid_input") from exc
        if item.expects == "list" and not isinstance(parsed, list):
            raise WorkflowError(f"{item.label} 必须是 JSON 数组。", code="invalid_input")
        # An empty list is allowed through: the graph reports it as a failed node with a message
        # the workflow page can show, instead of a bare form validation error.
        if item.required and parsed is None:
            raise WorkflowError(f"缺少必填参数：{item.label}。", code="invalid_input")
        return parsed

    raise WorkflowError(f"未知字段类型：{item.type}。", code="invalid_input")


__all__ = [
    "FIELD_TYPES",
    "WorkflowField",
    "WorkflowSpec",
    "WORKFLOWS",
    "get_graph",
    "get_workflow",
    "list_workflows",
    "validate_workflow_inputs",
]
