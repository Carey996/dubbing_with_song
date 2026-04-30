import assert from 'node:assert/strict';

import {
  buildSegmentProgressBackground,
  filterSegmentsByChapter,
  getClosestSegmentIndexByViewportCenter,
  getNearestLyricSegmentIndex,
  getSegmentKindLabel,
  getSelectedSegment,
  normalizeSegmentIndex,
  shouldShowTtsControls,
} from './segmentNavigator.js';

const segments = [
  { id: 'seg-001', type: 'narration', text: '普通文本段落', chapterId: 'chap-001', chapterTitle: '第一章' },
  { id: 'seg-002', type: 'lyric', text: '歌词段落', chapterId: 'chap-001', chapterTitle: '第一章' },
  { id: 'seg-003', type: 'narration', text: '结尾旁白', chapterId: 'chap-002', chapterTitle: '第二章' },
];

assert.equal(normalizeSegmentIndex(-10, segments), 0);
assert.equal(normalizeSegmentIndex(99, segments), 2);
assert.equal(normalizeSegmentIndex(Number.NaN, segments), 0);
assert.equal(normalizeSegmentIndex(2, []), 0);

assert.deepEqual(getSelectedSegment(segments, 1), { segment: segments[1], index: 1 });
assert.equal(getSelectedSegment([], 1).segment, null);

assert.equal(getSegmentKindLabel(segments[0]), '普通文本');
assert.equal(getSegmentKindLabel(segments[1]), '歌词');
assert.equal(shouldShowTtsControls(segments[0]), true);
assert.equal(shouldShowTtsControls(segments[1]), false);
assert.equal(getNearestLyricSegmentIndex(segments, 0), 1);
assert.equal(getNearestLyricSegmentIndex(segments, 2), 1);
assert.equal(getNearestLyricSegmentIndex([segments[0], segments[2]], 0), 0);
assert.equal(
  buildSegmentProgressBackground(segments),
  'linear-gradient(to right, #dbe6ee 0.00% 25.00%, #f2a65a 25.00% 75.00%, #dbe6ee 75.00% 100.00%)',
);
assert.equal(buildSegmentProgressBackground([segments[0], segments[2]]), '#dbe6ee');
assert.deepEqual(filterSegmentsByChapter(segments, null), segments);
assert.deepEqual(filterSegmentsByChapter(segments, { id: 'chap-001', title: '第一章' }), [segments[0], segments[1]]);
assert.deepEqual(filterSegmentsByChapter(segments, { id: 'missing', title: '第一章' }), [segments[0], segments[1]]);
assert.deepEqual(filterSegmentsByChapter(segments, { id: 'missing', title: '不存在' }), []);

assert.equal(
  getClosestSegmentIndexByViewportCenter([
    { index: 0, top: -120, bottom: 20 },
    { index: 1, top: 40, bottom: 220 },
    { index: 2, top: 240, bottom: 520 },
  ], 400),
  1,
);
assert.equal(
  getClosestSegmentIndexByViewportCenter([
    { index: 0, top: -120, bottom: 20 },
    { index: 1, top: 40, bottom: 120 },
    { index: 2, top: 180, bottom: 260 },
  ], 400),
  2,
);
assert.equal(getClosestSegmentIndexByViewportCenter([], 400), 0);
