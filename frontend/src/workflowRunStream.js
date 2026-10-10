export const TERMINAL_RUN_STATUSES = new Set(['succeeded', 'failed', 'interrupted']);

export function isRunTerminal(status) {
  return TERMINAL_RUN_STATUSES.has(status);
}

export function createRunState({ runId = '', workflowId = '' } = {}) {
  return {
    runId,
    workflowId,
    status: 'queued',
    steps: [],
    warnings: [],
    result: null,
    error: '',
    errorCode: '',
    failedNode: '',
    lastSeq: 0,
    startedAt: '',
    finishedAt: '',
  };
}

export function runStateFromSnapshot(snapshot) {
  const state = createRunState({ runId: snapshot?.runId || '', workflowId: snapshot?.workflowId || '' });
  return {
    ...state,
    status: snapshot?.status || 'queued',
    warnings: Array.isArray(snapshot?.warnings) ? snapshot.warnings : [],
    result: snapshot?.result || null,
    error: snapshot?.error || '',
    errorCode: snapshot?.errorCode || '',
    failedNode: snapshot?.failedNode || '',
    lastSeq: Number(snapshot?.lastSeq || 0),
    startedAt: snapshot?.startedAt || '',
    finishedAt: snapshot?.finishedAt || '',
    steps: Array.isArray(snapshot?.steps) ? snapshot.steps.map((step, index) => ({
      id: `snapshot-${index}`,
      seq: index,
      node: step.node,
      label: step.label,
      status: step.status,
      detail: step.detail || '',
      elapsedMs: step.elapsedMs ?? null,
      startedAt: step.startedAt || '',
      finishedAt: step.finishedAt || null,
    })) : [],
  };
}

export function applyRunEvent(state, event) {
  if (!event || typeof event !== 'object') return state;
  const seq = Number(event.seq || 0);
  if (seq && seq <= state.lastSeq) return state;
  const next = { ...state, lastSeq: seq || state.lastSeq };

  switch (event.type) {
    case 'run_started':
      return { ...next, status: 'running', startedAt: event.at || '' };
    case 'node_started':
      return { ...next, status: 'running', steps: [...next.steps, startStep(event)] };
    case 'node_finished':
      return { ...next, steps: finishStep(next.steps, event, event.status || 'succeeded') };
    case 'node_failed':
      return {
        ...next,
        steps: finishStep(next.steps, event, 'failed'),
        failedNode: event.node || next.failedNode,
      };
    case 'warning':
      return appendWarning(next, event.detail);
    case 'run_finished': {
      const payload = event.payload || {};
      return {
        ...next,
        status: 'succeeded',
        finishedAt: event.at || '',
        result: payload.result || next.result,
        warnings: dedupe([...(payload.warnings || next.warnings)]),
      };
    }
    case 'run_failed': {
      const payload = event.payload || {};
      return {
        ...next,
        status: 'failed',
        startedAt: next.startedAt,
        finishedAt: event.at || '',
        error: payload.error || event.detail || '工作流执行失败。',
        errorCode: payload.errorCode || '',
        failedNode: payload.node || event.node || next.failedNode,
        warnings: dedupe([...(payload.warnings || next.warnings)]),
      };
    }
    default:
      return next;
  }
}

export function reduceRunEvents(state, events = []) {
  return events.reduce((current, event) => applyRunEvent(current, event), state);
}

function startStep(event) {
  return {
    id: `seq-${event.seq}`,
    seq: event.seq,
    node: event.node || '',
    label: event.label || event.node || '',
    status: 'running',
    detail: '',
    elapsedMs: null,
    startedAt: event.at || '',
    finishedAt: null,
  };
}

function finishStep(steps, event, status) {
  const next = [...steps];
  for (let index = next.length - 1; index >= 0; index -= 1) {
    if (next[index].status === 'running' && next[index].node === event.node) {
      next[index] = {
        ...next[index],
        status,
        detail: event.detail || '',
        elapsedMs: event.elapsedMs ?? null,
        finishedAt: event.at || '',
      };
      return next;
    }
  }
  // A terminal event without a matching start (e.g. a replayed tail) still deserves a row.
  return [...next, {
    id: `seq-${event.seq}`,
    seq: event.seq,
    node: event.node || '',
    label: event.label || event.node || '',
    status,
    detail: event.detail || '',
    elapsedMs: event.elapsedMs ?? null,
    startedAt: '',
    finishedAt: event.at || '',
  }];
}

function appendWarning(state, detail) {
  if (!detail) return state;
  return { ...state, warnings: dedupe([...state.warnings, String(detail)]) };
}

function dedupe(values) {
  return [...new Set(values.filter(Boolean))];
}

export function parseSseFrames(buffer) {
  const events = [];
  let rest = String(buffer ?? '').replace(/\r\n/g, '\n');
  let separator = rest.indexOf('\n\n');
  while (separator !== -1) {
    const frame = rest.slice(0, separator);
    rest = rest.slice(separator + 2);
    const parsed = parseFrame(frame);
    if (parsed) events.push(parsed);
    separator = rest.indexOf('\n\n');
  }
  return { events, rest };
}

function parseFrame(frame) {
  let id = 0;
  let type = 'message';
  const dataLines = [];
  for (const rawLine of frame.split('\n')) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(':')) continue;
    const separator = line.indexOf(':');
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? '' : line.slice(separator + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'id') id = Number(value) || 0;
    else if (field === 'event') type = value;
    else if (field === 'data') dataLines.push(value);
  }
  if (!dataLines.length) return null;
  let data;
  try {
    data = JSON.parse(dataLines.join('\n'));
  } catch {
    return null;
  }
  return { ...data, id, type: data.type || type };
}

export async function streamRunEvents({
  runId,
  signal,
  lastEventId = 0,
  onEvent,
  fetchImpl = globalThis.fetch,
  baseUrl = '',
}) {
  const headers = { Accept: 'text/event-stream' };
  if (lastEventId > 0) headers['Last-Event-ID'] = String(lastEventId);
  const response = await fetchImpl(`${baseUrl}/api/workflows/runs/${encodeURIComponent(runId)}/events`, {
    headers,
    signal,
  });
  if (!response.ok) {
    throw new Error(`读取工作流事件流失败（HTTP ${response.status}）。`);
  }
  if (!response.body || typeof response.body.getReader !== 'function') {
    throw new Error('当前环境不支持流式读取工作流事件。');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseFrames(buffer);
    buffer = rest;
    for (const event of events) onEvent(event);
  }
  buffer += decoder.decode();
  for (const event of parseSseFrames(buffer).events) onEvent(event);
}

export async function followRunEvents({
  runId,
  readLastSeq,
  isTerminal,
  onEvent,
  signal,
  fetchImpl = globalThis.fetch,
  baseUrl = '',
  sleep,
  retryDelayMs = 800,
  maxAttempts = 6,
}) {
  const wait = sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (signal?.aborted) return 'aborted';
    try {
      await streamRunEvents({
        runId,
        signal,
        lastEventId: readLastSeq(),
        onEvent,
        fetchImpl,
        baseUrl,
      });
    } catch (error) {
      if (signal?.aborted) return 'aborted';
      if (attempt === maxAttempts - 1) throw error;
      await wait(retryDelayMs);
      continue;
    }
    if (isTerminal()) return 'finished';
    if (attempt === maxAttempts - 1) {
      throw new Error('工作流事件流已断开，但运行还没有结束。');
    }
    await wait(retryDelayMs);
  }
  return 'aborted';
}
