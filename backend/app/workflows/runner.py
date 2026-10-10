"""Run registry for the workflow agents.

A run executes ``graph.stream(...)`` on a worker thread and publishes every node transition as a
numbered event. Subscribers (the SSE endpoint) get the events live through their own asyncio
queue, and the full event log is persisted to ``data/workflow_runs/<run_id>.json`` so a page
refresh — or a backend restart — can still replay what happened.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..config import resolve_data_dir
from . import registry
from .common import now_iso

RUNS_DIR = resolve_data_dir() / "workflow_runs"
DEFAULT_RUN_CONCURRENCY = 4
DEFAULT_RUN_KEEP_COUNT = 50
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "interrupted"})
LIST_CHANNELS = ("steps", "warnings")


def run_concurrency() -> int:
    return _positive_env("WORKFLOW_RUN_CONCURRENCY", DEFAULT_RUN_CONCURRENCY)


def run_keep_count() -> int:
    return _positive_env("WORKFLOW_RUN_KEEP_COUNT", DEFAULT_RUN_KEEP_COUNT)


def _positive_env(name: str, fallback: int) -> int:
    try:
        configured = int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return fallback
    return configured if configured > 0 else fallback


@dataclass
class RunRecord:
    id: str
    workflow_id: str
    inputs: dict
    status: str = "queued"
    createdAt: str = field(default_factory=now_iso)
    startedAt: str | None = None
    finishedAt: str | None = None
    error: str | None = None
    errorCode: str | None = None
    failedNode: str | None = None
    result: dict | None = None
    warnings: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    seq: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    subscribers: dict = field(default_factory=dict, repr=False)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def emit(self, event_type: str, **fields: Any) -> dict:
        with self.lock:
            self.seq += 1
            event = {
                "seq": self.seq,
                "runId": self.id,
                "workflowId": self.workflow_id,
                "type": event_type,
                "at": now_iso(),
                **fields,
            }
            self.events.append(event)
            targets = list(self.subscribers.items())

        for queue, loop in targets:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                # The subscriber's loop is gone; the SSE generator detaches on its own finally.
                continue

        persist_run(self)
        return event

    def attach_subscriber(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, after_seq: int):
        with self.lock:
            replay = [event for event in self.events if event["seq"] > after_seq]
            if self.is_terminal():
                return replay, queue, True
            self.subscribers[queue] = loop
        return replay, queue, False

    def detach_subscriber(self, queue: asyncio.Queue) -> None:
        with self.lock:
            self.subscribers.pop(queue, None)

    def finish_success(self, result: dict, warnings: list[str], steps: list[dict]) -> None:
        with self.lock:
            self.status = "succeeded"
            self.finishedAt = now_iso()
            self.result = result or {}
            self.warnings = list(warnings)
            self.steps = list(steps)
        self.emit(
            "run_finished",
            status="succeeded",
            detail=str(self.result.get("message") or "工作流执行完成。"),
            payload={"result": self.result, "warnings": self.warnings, "steps": self.steps},
        )

    def finish_failure(self, message: str, code: str, node: str | None, warnings: list[str], steps: list[dict]) -> None:
        with self.lock:
            self.status = "failed"
            self.finishedAt = now_iso()
            self.error = message
            self.errorCode = code
            self.failedNode = node
            self.warnings = list(warnings)
            self.steps = list(steps)
        self.emit(
            "run_failed",
            status="failed",
            node=node,
            detail=message,
            payload={
                "error": message,
                "errorCode": code,
                "node": node,
                "warnings": self.warnings,
                "steps": self.steps,
            },
        )

    def snapshot(self, include_events: bool = False) -> dict:
        with self.lock:
            payload = {
                "runId": self.id,
                "workflowId": self.workflow_id,
                "status": self.status,
                "inputs": self.inputs,
                "createdAt": self.createdAt,
                "startedAt": self.startedAt,
                "finishedAt": self.finishedAt,
                "error": self.error,
                "errorCode": self.errorCode,
                "failedNode": self.failedNode,
                "result": self.result,
                "warnings": list(self.warnings),
                "steps": list(self.steps),
                "lastSeq": self.seq,
            }
            if include_events:
                payload["events"] = list(self.events)
            return payload


class WorkflowRunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = threading.RLock()
        self._semaphore = threading.Semaphore(run_concurrency())
        self._loaded = False

    def create_run(self, workflow_id: str, inputs: dict) -> RunRecord:
        # Raises WorkflowError for an unknown workflow or invalid parameters.
        registry.get_workflow(workflow_id)
        validated = registry.validate_workflow_inputs(workflow_id, inputs)

        record = RunRecord(id=uuid.uuid4().hex[:12], workflow_id=workflow_id, inputs=validated)
        with self._lock:
            self._runs[record.id] = record
        persist_run(record)

        thread = threading.Thread(target=self._execute, args=(record,), name=f"workflow-{record.id}", daemon=True)
        thread.start()
        return record

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            record = self._runs.get(str(run_id or ""))
        if record is not None:
            return record
        return self._load_from_disk(str(run_id or ""))

    def list_recent(self, limit: int = 20) -> list[dict]:
        self._ensure_loaded()
        with self._lock:
            records = list(self._runs.values())
        records.sort(key=lambda item: item.createdAt, reverse=True)
        return [record.snapshot() for record in records[: max(1, limit)]]

    def _execute(self, record: RunRecord) -> None:
        spec = None
        merged_state: dict = {"data": {}}
        failed_node: dict = {}

        self._semaphore.acquire()
        try:
            with record.lock:
                record.status = "running"
                record.startedAt = now_iso()
            record.emit("run_started", detail=f"开始执行「{record.workflow_id}」")

            spec = registry.get_workflow(record.workflow_id)
            graph = registry.get_graph(record.workflow_id)

            def emit(event_type: str, node: str | None = None, label: str | None = None, status: str | None = None,
                     detail: str = "", elapsedMs: int | None = None, **_extra: Any) -> None:
                if event_type == "node_failed":
                    failed_node["node"] = node
                record.emit(
                    event_type,
                    node=node,
                    label=label,
                    status=status,
                    detail=detail,
                    elapsedMs=elapsedMs,
                )

            initial_state = {
                "runId": record.id,
                "workflowId": record.workflow_id,
                "inputs": record.inputs,
                "data": {},
                "steps": [],
                "warnings": [],
                "error": None,
            }
            stream = graph.stream(
                initial_state,
                config={"configurable": {"emit": emit}},
                stream_mode="updates",
            )
            for chunk in stream:
                for _node_name, update in (chunk or {}).items():
                    if isinstance(update, dict):
                        merged_state = merge_state_update(merged_state, update)

            record.finish_success(
                dict(merged_state.get("result") or {}),
                [str(item) for item in merged_state.get("warnings") or []],
                list(merged_state.get("steps") or []),
            )
        except Exception as exc:  # noqa: BLE001 - a failed run must still be reported, never crash the worker
            message = getattr(exc, "message", None) or str(exc)
            code = getattr(exc, "code", None) or "workflow_failed"
            if spec is not None and spec.cleanup is not None:
                try:
                    spec.cleanup(merged_state, exc)
                except Exception:  # noqa: BLE001 - cleanup must not mask the original error
                    pass
            record.finish_failure(
                message,
                code,
                failed_node.get("node") or getattr(exc, "node", None),
                [str(item) for item in merged_state.get("warnings") or []],
                list(merged_state.get("steps") or []),
            )
        finally:
            self._semaphore.release()
            prune_runs()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
        for payload in iter_persisted_runs():
            record = record_from_payload(payload)
            if record is None:
                continue
            self._adopt(record)

    def _adopt(self, record: RunRecord) -> RunRecord:
        """Register a record loaded from disk, marking runs abandoned by a restart as failed."""

        mark_interrupted(record)
        with self._lock:
            return self._runs.setdefault(record.id, record)

    def _load_from_disk(self, run_id: str) -> RunRecord | None:
        if not run_id or not run_id.replace("-", "").isalnum():
            return None
        path = RUNS_DIR / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        record = record_from_payload(payload)
        if record is None:
            return None
        return self._adopt(record)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._runs.clear()
            self._loaded = False


run_registry = WorkflowRunRegistry()


def merge_state_update(state: dict, update: dict) -> dict:
    """Merge one graph update chunk the way the graph's channels do.

    ``steps`` and ``warnings`` are appended; everything else is overwritten. The runner keeps this
    merged copy because ``graph.stream`` raises out of the loop when a node fails, which would
    otherwise lose the state the cleanup hook needs (e.g. the render id to mark as failed).
    """

    merged = dict(state)
    for key, value in update.items():
        if key in LIST_CHANNELS and isinstance(value, list):
            merged[key] = [*(merged.get(key) or []), *value]
        else:
            merged[key] = value
    return merged


def persist_run(record: RunRecord) -> None:
    """Atomically write one run snapshot; a crash must not leave a half-written JSON file."""

    payload = record.snapshot(include_events=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{record.id}.json"
    temporary = RUNS_DIR / f".{record.id}.tmp"
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # Persistence is a convenience for replay; a full disk must not fail the run itself.
        return


def record_from_payload(payload: Any) -> RunRecord | None:
    if not isinstance(payload, dict):
        return None
    run_id = str(payload.get("runId") or payload.get("id") or "")
    workflow_id = str(payload.get("workflowId") or "")
    if not run_id or not workflow_id:
        return None
    record = RunRecord(
        id=run_id,
        workflow_id=workflow_id,
        inputs=payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {},
        status=str(payload.get("status") or "queued"),
        createdAt=str(payload.get("createdAt") or now_iso()),
        startedAt=payload.get("startedAt"),
        finishedAt=payload.get("finishedAt"),
        error=payload.get("error"),
        errorCode=payload.get("errorCode"),
        failedNode=payload.get("failedNode"),
        result=payload.get("result") if isinstance(payload.get("result"), dict) else None,
        warnings=[str(item) for item in payload.get("warnings") or []],
        steps=[item for item in payload.get("steps") or [] if isinstance(item, dict)],
        events=[item for item in payload.get("events") or [] if isinstance(item, dict)],
        seq=int(payload.get("lastSeq") or len(payload.get("events") or []) or 0),
    )
    return record


def mark_interrupted(record: RunRecord) -> None:
    """A run persisted as queued/running means the backend restarted while it was executing."""

    if record.is_terminal():
        return
    record.status = "interrupted"
    record.error = record.error or "后端在运行过程中重启，本次运行已中断。"
    record.errorCode = "interrupted"
    record.finishedAt = record.finishedAt or now_iso()
    record.emit(
        "run_failed",
        status="failed",
        detail=record.error,
        payload={"error": record.error, "errorCode": "interrupted", "node": None, "warnings": [], "steps": []},
    )


def iter_persisted_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    payloads: list[dict] = []
    for path in sorted(RUNS_DIR.glob("*.json")):
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return payloads


def prune_runs(keep: int | None = None) -> list[str]:
    keep_count = run_keep_count() if keep is None else max(1, keep)
    if not RUNS_DIR.exists():
        return []

    paths = sorted(RUNS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    removed = []
    for path in paths[keep_count:]:
        try:
            path.unlink()
            removed.append(path.stem)
        except OSError:
            continue
    return removed


def load_persisted_runs() -> int:
    run_registry._ensure_loaded()
    return len(run_registry.list_recent(limit=1000))
