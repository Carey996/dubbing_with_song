from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class StepRecord(TypedDict, total=False):
    """One executed graph node, as shown on the workflow page timeline."""

    node: str
    label: str
    status: str  # running | succeeded | failed | skipped
    startedAt: str
    finishedAt: str | None
    detail: str
    elapsedMs: int | None


class RunEvent(TypedDict, total=False):
    """One SSE frame. ``seq`` is monotonic per run and drives ``Last-Event-ID`` replay."""

    seq: int
    runId: str
    workflowId: str
    type: str
    at: str
    node: str
    label: str
    status: str
    detail: str
    elapsedMs: int | None
    payload: dict[str, Any]


class WorkflowState(TypedDict, total=False):
    """Graph state shared by every workflow agent.

    ``steps`` and ``warnings`` use ``operator.add`` so node deltas append instead of
    replacing the accumulated list. Every other key is a normal overwrite channel.

    ``data`` carries intermediate values between nodes (project handles, chapters, parsed
    LLM results). It is intentionally never serialised: it can hold ``TextChapter``
    instances, while only the ``result`` dict is exposed over HTTP.
    """

    runId: str
    workflowId: str
    projectId: str
    inputs: dict[str, Any]
    steps: Annotated[list[StepRecord], operator.add]
    data: dict[str, Any]
    warnings: Annotated[list[str], operator.add]
    result: dict[str, Any]
    error: str | None


TERMINAL_EVENT_TYPES = frozenset({"run_finished", "run_failed"})
