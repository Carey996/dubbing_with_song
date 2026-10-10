import assert from 'node:assert/strict';

import {
  applyRunEvent,
  createRunState,
  followRunEvents,
  isRunTerminal,
  parseSseFrames,
  reduceRunEvents,
  runStateFromSnapshot,
  streamRunEvents,
} from './workflowRunStream.js';

function sseResponse(chunks, { status = 200 } = {}) {
  const encoder = new TextEncoder();
  return {
    ok: status >= 200 && status < 300,
    status,
    body: new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
  };
}

function sseFrame(event, seq) {
  return `id: ${seq}\nevent: ${event.type}\ndata: ${JSON.stringify({ ...event, seq })}\n\n`;
}

const initial = createRunState({ runId: 'r1', workflowId: 'chapter-split' });
assert.deepEqual(initial.steps, []);
assert.equal(initial.status, 'queued');

assert.equal(isRunTerminal('succeeded'), true);
assert.equal(isRunTerminal('failed'), true);
assert.equal(isRunTerminal('running'), false);

let state = applyRunEvent(initial, { seq: 1, type: 'run_started', at: 't1' });
assert.equal(state.status, 'running');

state = applyRunEvent(state, { seq: 2, type: 'node_started', node: 'load_project', label: '读取项目', at: 't2' });
assert.deepEqual(state.steps.map((step) => [step.node, step.status]), [['load_project', 'running']]);

state = applyRunEvent(state, {
  seq: 3,
  type: 'node_finished',
  node: 'load_project',
  label: '读取项目',
  status: 'succeeded',
  detail: '读取项目 p1（12 字）',
  elapsedMs: 12,
  at: 't3',
});
assert.equal(state.steps[0].status, 'succeeded');
assert.equal(state.steps[0].detail, '读取项目 p1（12 字）');
assert.equal(state.steps[0].elapsedMs, 12);
assert.equal(state.steps[0].finishedAt, 't3');

// a repeated node name (validate_json runs again after repair_json) must not overwrite the first
state = applyRunEvent(state, { seq: 4, type: 'node_started', node: 'validate_json', label: '校验', at: 't4' });
state = applyRunEvent(state, { seq: 5, type: 'node_started', node: 'repair_json', label: '修复', at: 't5' });
state = applyRunEvent(state, { seq: 6, type: 'node_started', node: 'validate_json', label: '校验', at: 't6' });
state = applyRunEvent(state, { seq: 7, type: 'node_finished', node: 'validate_json', status: 'succeeded', detail: 'ok', at: 't7' });
const validateSteps = state.steps.filter((step) => step.node === 'validate_json');
assert.equal(validateSteps.length, 2);
assert.deepEqual(validateSteps.map((step) => step.status), ['running', 'succeeded']);

// duplicate / out-of-order events are ignored
const beforeDuplicate = state;
state = applyRunEvent(state, { seq: 3, type: 'node_failed', node: 'load_project', detail: 'stale' });
assert.equal(state, beforeDuplicate, 'a replayed seq must be ignored');
assert.equal(state.lastSeq, 7);

state = applyRunEvent(state, { seq: 8, type: 'warning', detail: '注意 A', at: 't8' });
state = applyRunEvent(state, { seq: 9, type: 'warning', detail: '注意 A', at: 't9' });
assert.deepEqual(state.warnings, ['注意 A'], 'warnings are de-duplicated');

state = applyRunEvent(state, {
  seq: 10,
  type: 'run_finished',
  at: 't10',
  payload: { result: { chapterCount: 2 }, warnings: ['注意 A', '注意 B'], steps: [] },
});
assert.equal(state.status, 'succeeded');
assert.deepEqual(state.result, { chapterCount: 2 });
assert.deepEqual(state.warnings, ['注意 A', '注意 B']);
assert.equal(state.finishedAt, 't10');

let failed = applyRunEvent(createRunState({ runId: 'r2', workflowId: 'render' }), {
  seq: 1,
  type: 'run_failed',
  at: 't1',
  payload: { error: '缺少 TTS_API_KEY', errorCode: 'workflow_failed', node: 'build_narration' },
});
assert.equal(failed.status, 'failed');
assert.equal(failed.error, '缺少 TTS_API_KEY');
assert.equal(failed.failedNode, 'build_narration');
assert.equal(failed.errorCode, 'workflow_failed');

assert.equal(applyRunEvent(initial, null), initial);

const reduced = reduceRunEvents(createRunState({ runId: 'r3', workflowId: 'x' }), [
  { seq: 1, type: 'node_started', node: 'a', label: 'A' },
  { seq: 2, type: 'node_failed', node: 'a', label: 'A', detail: '炸了' },
]);
assert.equal(reduced.steps[0].status, 'failed');
assert.equal(reduced.failedNode, 'a');

// a node_finished without a matching node_started still gets a row (replayed tail)
const tailOnly = applyRunEvent(createRunState({ runId: 'r4' }), {
  seq: 9,
  type: 'node_finished',
  node: 'ghost',
  label: '幽灵',
  status: 'skipped',
  detail: '跳过',
});
assert.deepEqual(tailOnly.steps.map((step) => [step.node, step.status]), [['ghost', 'skipped']]);

const snapshotState = runStateFromSnapshot({
  runId: 'r5',
  workflowId: 'render',
  status: 'failed',
  lastSeq: 6,
  error: 'boom',
  errorCode: 'workflow_failed',
  failedNode: 'build_narration',
  warnings: ['w'],
  steps: [{ node: 'prepare_render', label: '准备', status: 'succeeded', detail: 'ok', elapsedMs: 3 }],
});
assert.equal(snapshotState.status, 'failed');
assert.equal(snapshotState.lastSeq, 6);
assert.equal(snapshotState.steps[0].label, '准备');
assert.equal(snapshotState.steps[0].id, 'snapshot-0');

// ---- SSE frame parsing -------------------------------------------------------------

assert.deepEqual(parseSseFrames('id: 1\nevent: run_started\ndata: {"type":"run_started"}\n\n'), {
  events: [{ type: 'run_started', id: 1 }],
  rest: '',
});

const partial = parseSseFrames('id: 1\nevent: a\ndata: {"type":"a"}\n\nid: 2\nevent: b\ndata: {"type":"b"}\n');
assert.equal(partial.events.length, 1);
assert.equal(partial.events[0].type, 'a');
assert.equal(partial.rest, 'id: 2\nevent: b\ndata: {"type":"b"}\n');

const withPing = parseSseFrames(': ping\n\nid: 4\nevent: node_finished\ndata: {"type":"node_finished","node":"a"}\n\n');
assert.deepEqual(withPing.events.map((event) => event.type), ['node_finished']);
assert.equal(withPing.events[0].id, 4);

assert.deepEqual(parseSseFrames('data: not-json\n\n').events, [], 'a malformed data line is skipped');
assert.deepEqual(parseSseFrames('').events, []);

// CRLF frames (some proxies rewrite the separators)
assert.equal(parseSseFrames('id: 3\r\nevent: x\r\ndata: {"type":"x"}\r\n\r\n').events.length, 1);

// ---- streamRunEvents ---------------------------------------------------------------

const received = [];
let capturedHeaders = null;
let capturedUrl = '';
await streamRunEvents({
  runId: 'r6',
  lastEventId: 4,
  onEvent: (event) => received.push(event),
  fetchImpl: async (url, options) => {
    capturedUrl = url;
    capturedHeaders = options.headers;
    return sseResponse([
      sseFrame({ type: 'node_finished', node: 'a', status: 'succeeded' }, 5),
      sseFrame({ type: 'run_finished' }, 6),
    ]);
  },
});

assert.equal(capturedUrl, '/api/workflows/runs/r6/events');
assert.equal(capturedHeaders['Last-Event-ID'], '4');
assert.deepEqual(received.map((event) => [event.seq, event.type]), [[5, 'node_finished'], [6, 'run_finished']]);

let noHeader = null;
await streamRunEvents({
  runId: 'r6',
  onEvent: () => {},
  fetchImpl: async (_url, options) => {
    noHeader = options.headers;
    return sseResponse(['']);
  },
});
assert.equal(noHeader['Last-Event-ID'], undefined);

await assert.rejects(
  () => streamRunEvents({ runId: 'r7', onEvent: () => {}, fetchImpl: async () => ({ ok: false, status: 500, body: null }) }),
  /HTTP 500/,
);
await assert.rejects(
  () => streamRunEvents({ runId: 'r7', onEvent: () => {}, fetchImpl: async () => ({ ok: true, status: 200, body: {} }) }),
  /不支持流式读取/,
);

// ---- followRunEvents ---------------------------------------------------------------

let attempts = 0;
const reconnected = [];
const outcome = await followRunEvents({
  runId: 'r8',
  readLastSeq: () => reconnected.at(-1)?.seq || 0,
  isTerminal: () => reconnected.some((event) => event.type === 'run_finished'),
  onEvent: (event) => reconnected.push(event),
  sleep: async () => {},
  retryDelayMs: 1,
  fetchImpl: async () => {
    attempts += 1;
    if (attempts === 1) {
      // first connection drops before delivering the terminal event
      return sseResponse([sseFrame({ type: 'node_started', node: 'a' }, 1)]);
    }
    return sseResponse([sseFrame({ type: 'run_finished' }, 2)]);
  },
});

assert.equal(outcome, 'finished');
assert.equal(attempts, 2);
assert.deepEqual(reconnected.map((event) => event.seq), [1, 2]);

const aborted = await followRunEvents({
  runId: 'r9',
  readLastSeq: () => 0,
  isTerminal: () => false,
  onEvent: () => {},
  signal: { aborted: true },
  fetchImpl: async () => {
    throw new Error('must not be called');
  },
});

assert.equal(aborted, 'aborted');

await assert.rejects(
  () => followRunEvents({
    runId: 'r10',
    readLastSeq: () => 0,
    isTerminal: () => false,
    onEvent: () => {},
    sleep: async () => {},
    maxAttempts: 2,
    fetchImpl: async () => sseResponse([sseFrame({ type: 'node_started', node: 'a' }, 1)]),
  }),
  /还没有结束|没有结束/,
);

console.log('workflowRunStream tests passed');
