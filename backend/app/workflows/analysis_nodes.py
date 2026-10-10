"""LLM analysis nodes shared by the 全篇 and 单章 analysis workflows.

Both workflows chunk text, ask the configured LangChain analyzer for JSON, and retry a chunk
whose answer does not parse. Keeping the nodes here means the repair loop only exists once.
"""

from __future__ import annotations

from ..services.analyzer import (
    LlmAnalysis,
    analyzer_chunk_chars,
    build_analyzer_llm,
    build_chunk_inputs,
    merge_llm_analyses,
    parse_llm_analysis,
)
from .common import WorkflowError, merge_data

MAX_LLM_ATTEMPTS = 3
REPAIR_INSTRUCTION = (
    "\n\n上一次的输出无法解析为合法 JSON。请重新输出，并且只输出一个完整合法的 JSON object，"
    "不要输出 Markdown 代码块、解释或任何多余文字。"
)


def resolve_chunk_chars(state) -> int:
    configured = (state.get("inputs") or {}).get("chunkChars")
    if configured in (None, ""):
        return analyzer_chunk_chars()
    try:
        value = int(configured)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("chunkChars 必须是整数。", code="invalid_input") from exc
    if value < 200:
        raise WorkflowError("chunkChars 不能小于 200，否则会把章节切得过碎。", code="invalid_input")
    return value


def node_build_chunks(state) -> dict:
    chapters = state["data"]["chapters"]
    chunk_chars = resolve_chunk_chars(state)
    chunk_inputs = build_chunk_inputs(chapters, chunk_chars)
    return {
        **merge_data(state, chunkInputs=chunk_inputs, chunkChars=chunk_chars),
        "__detail__": f"每块 {chunk_chars} 字，共 {len(chunk_inputs)} 个 LLM 输入块",
    }


def node_llm_analyze(state) -> dict:
    chunk_inputs = state["data"]["chunkInputs"]
    prompt, llm = build_analyzer_llm()
    raw_contents = [_invoke(prompt, llm, chunk) for _chapter, chunk in chunk_inputs]
    return {
        **merge_data(state, rawContents=raw_contents, attempts=1),
        "__detail__": f"LLM 已返回 {len(raw_contents)} 个文本块的原始结果",
    }


def _invoke(prompt, llm, chunk: str) -> str:
    response = (prompt | llm).invoke({"text": chunk})
    content = getattr(response, "content", response)
    if isinstance(content, list):
        # Some chat models answer with content blocks instead of a plain string.
        return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content)
    return str(content)


def node_validate_json(state) -> dict:
    raw_contents = state["data"]["rawContents"]
    parsed: list[LlmAnalysis | None] = []
    error_details: list[dict] = []
    for index, content in enumerate(raw_contents):
        try:
            parsed.append(parse_llm_analysis(content))
        except Exception as exc:  # noqa: BLE001 - any parse failure is a retry candidate
            parsed.append(None)
            error_details.append({"index": index, "message": str(exc)})

    attempts = int(state["data"].get("attempts") or 1)
    detail = f"第 {attempts} 次解析：{len(parsed) - len(error_details)}/{len(parsed)} 个文本块成功"
    if error_details:
        detail += f"，{len(error_details)} 个需要重试"
    return {
        **merge_data(
            state,
            parsed=parsed,
            parseErrors=[item["index"] for item in error_details],
            parseErrorDetails=error_details,
        ),
        "__detail__": detail,
    }


def route_after_validate(state) -> str:
    data = state["data"]
    if not (data.get("parseErrors") or []):
        return "merge"
    attempts = int(data.get("attempts") or 1)
    return "repair" if attempts < MAX_LLM_ATTEMPTS else "give_up"


def node_repair_json(state) -> dict:
    data = state["data"]
    failing_indexes = list(data.get("parseErrors") or [])
    chunk_inputs = data["chunkInputs"]
    raw_contents = list(data["rawContents"])
    prompt, llm = build_analyzer_llm()
    for index in failing_indexes:
        _chapter, chunk = chunk_inputs[index]
        raw_contents[index] = _invoke(prompt, llm, chunk + REPAIR_INSTRUCTION)

    attempts = int(data.get("attempts") or 1) + 1
    return {
        **merge_data(state, rawContents=raw_contents, attempts=attempts),
        "__detail__": f"第 {attempts} 次尝试：重试 {len(failing_indexes)} 个 JSON 解析失败的文本块",
    }


def node_give_up(state) -> dict:
    details = state["data"].get("parseErrorDetails") or []
    first = details[0]["message"] if details else "未知解析错误"
    raise WorkflowError(
        f"LLM 连续 {MAX_LLM_ATTEMPTS} 次返回了无法解析的 JSON，最后一条错误：{first}",
        code="llm_json_invalid",
    )


def node_merge_analysis(state) -> dict:
    data = state["data"]
    chunk_inputs = data["chunkInputs"]
    parsed = data["parsed"]
    chapters = data["chapters"]

    analyzed = [
        (chapter, analysis)
        for (chapter, _chunk), analysis in zip(chunk_inputs, parsed)
        if isinstance(analysis, LlmAnalysis)
    ]
    analysis = merge_llm_analyses(analyzed, chapters, data["text"])
    segments = analysis.get("segments") or []
    lyric_count = len([segment for segment in segments if segment.get("type") == "lyric"])
    return {
        **merge_data(state, analysis=analysis),
        "__detail__": (
            f"合并出 {len(segments)} 个片段（歌词 {lyric_count} 段），"
            f"{len(analysis.get('songCandidates') or [])} 个歌曲候选"
        ),
    }
