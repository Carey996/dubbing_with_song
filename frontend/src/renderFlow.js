export function isRenderBusy({ busy = '', isTimelineSaving = false, isRendering = false }) {
  // Rendering starts after the timeline save resolves, and the save clears its own busy flag on
  // the way out. Without the explicit isRendering term the render button became clickable again
  // for the whole (multi-minute) POST /render, so a second click re-synthesized every chunk.
  return Boolean(busy) || isTimelineSaving || isRendering;
}

export async function runRenderFlow({ persist, render }) {
  const saved = await persist();
  if (!saved) {
    return { status: 'invalid-timeline' };
  }
  return { status: 'rendered', result: await render() };
}
