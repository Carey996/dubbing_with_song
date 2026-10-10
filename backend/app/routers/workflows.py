"""HTTP surface for the LangGraph workflow agents.

``POST /api/workflows/{id}/runs`` starts a run and returns immediately; the client then reads
``GET /api/workflows/runs/{run_id}/events`` as a Server-Sent Events stream that carries every node
transition. ``Last-Event-ID`` replays anything the client missed, which is also how a page refresh
rebuilds the timeline of an already finished run.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import StreamingResponse

from ..workflows import registry
from ..workflows.common import WorkflowError
from ..workflows.runner import run_registry
from ..workflows.state import TERMINAL_EVENT_TYPES

router = APIRouter(tags=["workflows"])

HEARTBEAT_SECONDS = 15

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get("/workflows")
def list_workflows_endpoint() -> dict:
    return {"workflows": registry.list_workflows()}


@router.get("/workflows/runs")
def list_workflow_runs_endpoint(limit: int = Query(default=20, ge=1, le=200)) -> dict:
    return {"runs": run_registry.list_recent(limit=limit)}


@router.post("/workflows/{workflow_id}/runs", status_code=202)
async def create_workflow_run_endpoint(workflow_id: str, request: Request) -> dict:
    inputs = await read_json_body(request)
    try:
        record = run_registry.create_run(workflow_id, inputs)
    except WorkflowError as exc:
        status_code = 404 if exc.code == "unknown_workflow" else 422
        raise HTTPException(status_code=status_code, detail=exc.message) from exc
    return record.snapshot()


@router.get("/workflows/runs/{run_id}")
def get_workflow_run_endpoint(run_id: str) -> dict:
    record = run_registry.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Workflow run not found.")
    return record.snapshot(include_events=True)


@router.get("/workflows/runs/{run_id}/events")
async def stream_workflow_run_endpoint(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0, description="只返回 seq 大于该值的事件。"),
) -> StreamingResponse:
    record = run_registry.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Workflow run not found.")

    last_event_id = max(parse_last_event_id(request.headers.get("last-event-id")), after)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    replay, queue, already_finished = record.attach_subscriber(queue, loop, last_event_id)

    async def event_stream():
        try:
            for event in replay:
                yield format_sse(event)
                if event.get("type") in TERMINAL_EVENT_TYPES:
                    return
            if already_finished:
                return

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield format_sse(event)
                if event.get("type") in TERMINAL_EVENT_TYPES:
                    return
        finally:
            record.detach_subscriber(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


async def read_json_body(request: Request) -> dict:
    body = await request.body()
    if not body.strip():
        return {}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON。") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请求体必须是包含参数的 JSON object。")
    return payload


def parse_last_event_id(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def format_sse(event: dict) -> str:
    payload = json.dumps(event, ensure_ascii=False)
    return f"id: {event.get('seq', 0)}\nevent: {event.get('type', 'message')}\ndata: {payload}\n\n"
