export function normalizeSegmentIndex(index, segments) {
  if (!segments.length) return 0;
  const numeric = Number.isFinite(index) ? Math.round(index) : 0;
  return Math.min(Math.max(numeric, 0), segments.length - 1);
}

export function getSelectedSegment(segments, index) {
  if (!segments.length) {
    return { segment: null, index: 0 };
  }

  const normalizedIndex = normalizeSegmentIndex(index, segments);
  return { segment: segments[normalizedIndex], index: normalizedIndex };
}

export function filterSegmentsByChapter(segments, chapter) {
  if (!chapter) return segments;

  const byId = segments.filter((segment) => segment.chapterId === chapter.id);
  if (byId.length) return byId;

  const byTitle = segments.filter((segment) => segment.chapterTitle === chapter.title);
  return byTitle;
}

export function getSegmentKindLabel(segment) {
  return segment?.type === 'lyric' ? '歌词' : '普通文本';
}

export function getNearestLyricSegmentIndex(segments, startIndex) {
  if (!segments.length) return 0;
  const normalizedStart = normalizeSegmentIndex(startIndex, segments);
  if (segments[normalizedStart]?.type === 'lyric') return normalizedStart;

  for (let offset = 1; offset < segments.length; offset += 1) {
    const right = normalizedStart + offset;
    const left = normalizedStart - offset;
    if (right < segments.length && segments[right]?.type === 'lyric') return right;
    if (left >= 0 && segments[left]?.type === 'lyric') return left;
  }

  return normalizedStart;
}

export function getClosestSegmentIndexByViewportCenter(items, viewportHeight) {
  if (!items.length) return 0;
  const viewportCenter = Math.max(0, Number(viewportHeight || 0)) / 2;

  return items.reduce((closest, item) => {
    const itemCenter = (Number(item.top || 0) + Number(item.bottom || 0)) / 2;
    const distance = Math.abs(itemCenter - viewportCenter);
    return distance < closest.distance ? { index: item.index, distance } : closest;
  }, { index: items[0].index, distance: Number.POSITIVE_INFINITY }).index;
}
