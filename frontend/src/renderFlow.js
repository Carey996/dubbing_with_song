export async function runRenderFlow({ persist, render }) {
  const saved = await persist();
  if (!saved) {
    return { status: 'invalid-timeline' };
  }
  return { status: 'rendered', result: await render() };
}
