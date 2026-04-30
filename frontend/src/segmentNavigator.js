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

export function shouldShowTtsControls(segment) {
  return segment?.type !== 'lyric';
}

export function buildSegmentProgressBackground(segments) {
  if (!segments.length || !segments.some((segment) => segment.type === 'lyric')) {
    return '#dbe6ee';
  }

  if (segments.length === 1) {
    return segments[0].type === 'lyric' ? '#f2a65a' : '#dbe6ee';
  }

  const stops = [];
  const lastIndex = segments.length - 1;
  for (let index = 0; index < segments.length; index += 1) {
    const start = Math.max(0, ((index - 0.5) / lastIndex) * 100);
    const end = Math.min(100, ((index + 0.5) / lastIndex) * 100);
    const color = segments[index].type === 'lyric' ? '#f2a65a' : '#dbe6ee';
    const previous = stops[stops.length - 1];
    if (previous?.color === color && Math.abs(previous.end - start) < 0.01) {
      previous.end = end;
    } else {
      stops.push({ color, start, end });
    }
  }

  const cssStops = stops
    .map((stop) => `${stop.color} ${stop.start.toFixed(2)}% ${stop.end.toFixed(2)}%`)
    .join(', ');
  return `linear-gradient(to right, ${cssStops})`;
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
