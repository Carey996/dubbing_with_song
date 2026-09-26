import assert from 'node:assert/strict';

import { isRenderBusy, runRenderFlow } from './renderFlow.js';

const calls = [];

// Normal path: the timeline is persisted first, then rendering runs.
calls.length = 0;
const ok = await runRenderFlow({
  persist: async () => {
    calls.push('persist');
    return true;
  },
  render: async () => {
    calls.push('render');
    return { outputUrl: '/outputs/p1/r1/mixed.mp3' };
  },
});
assert.deepEqual(calls, ['persist', 'render']);
assert.equal(ok.status, 'rendered');
assert.deepEqual(ok.result, { outputUrl: '/outputs/p1/r1/mixed.mp3' });

// A failed save must stop the render: POST /render reads timeline.json from disk,
// so rendering anyway would silently produce audio from the previous timeline.
calls.length = 0;
const stale = await runRenderFlow({
  persist: async () => {
    calls.push('persist');
    return false;
  },
  render: async () => {
    calls.push('render');
    return {};
  },
});
assert.deepEqual(calls, ['persist']);
assert.equal(stale.status, 'invalid-timeline');
assert.equal(stale.result, undefined);

// A persist that throws (network error) must not start the render either.
calls.length = 0;
await assert.rejects(
  runRenderFlow({
    persist: async () => {
      calls.push('persist');
      throw new Error('PATCH /timeline failed');
    },
    render: async () => {
      calls.push('render');
      return {};
    },
  }),
  /PATCH \/timeline failed/,
);
assert.deepEqual(calls, ['persist']);

// The timeline save clears its own busy flag before the render request starts, so the render
// has to keep the buttons disabled on its own. Without this the 生成音频 button became
// clickable again for the whole POST /render and a second click re-synthesized every chunk.
assert.equal(isRenderBusy({ busy: 'rendering' }), true);
assert.equal(isRenderBusy({ isTimelineSaving: true }), true);
assert.equal(isRenderBusy({ busy: '', isTimelineSaving: false, isRendering: true }), true);
assert.equal(isRenderBusy({ busy: '', isTimelineSaving: false, isRendering: false }), false);
assert.equal(isRenderBusy({}), false);
