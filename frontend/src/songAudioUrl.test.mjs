import assert from 'node:assert/strict';

import { buildSegmentSongAudioUrl } from './songAudioUrl.js';

const project = { id: 'p1', updatedAt: '2026-01-01T00:00:00+00:00' };
const chapters = [
  {
    id: 'chap-001',
    chapterSong: { url: '/api/projects/p1/chapters/chap-001/song-file', updatedAt: '2026-01-01T00:00:00+00:00' },
  },
  {
    id: 'chap-002',
    chapterSong: { url: '/api/projects/p1/chapters/chap-002/song-file', updatedAt: '2026-01-01T00:00:00+00:00' },
  },
];

// A re-uploaded chapter MP3 must produce a different URL, otherwise the browser
// keeps a cached copy of the previous file.
const before = buildSegmentSongAudioUrl({
  segment: { chapterId: 'chap-001' },
  chapters,
  project,
  selectedChapterId: '',
  localChapterSongUrl: '',
  projectSongUrl: '/api/projects/p1/song-file?v=project',
});
const after = buildSegmentSongAudioUrl({
  segment: { chapterId: 'chap-001' },
  chapters: [
    {
      id: 'chap-001',
      chapterSong: { url: '/api/projects/p1/chapters/chap-001/song-file', updatedAt: '2026-02-02T00:00:00+00:00' },
    },
    chapters[1],
  ],
  project,
  selectedChapterId: '',
  localChapterSongUrl: '',
  projectSongUrl: '/api/projects/p1/song-file?v=project',
});

assert.notEqual(before, after);
assert.match(after, /chap-001\/song-file\?v=2026-02-02T00%3A00%3A00%2B00%3A00/);

// The freshly picked local file wins for the chapter it belongs to.
assert.equal(
  buildSegmentSongAudioUrl({
    segment: { chapterId: 'chap-001' },
    chapters,
    project,
    selectedChapterId: 'chap-001',
    localChapterSongUrl: 'blob:local',
    projectSongUrl: '/api/projects/p1/song-file?v=project',
  }),
  'blob:local',
);

// A segment without a chapter falls back to the project-level MP3.
assert.equal(
  buildSegmentSongAudioUrl({
    segment: {},
    chapters,
    project,
    selectedChapterId: '',
    localChapterSongUrl: '',
    projectSongUrl: '/api/projects/p1/song-file?v=project',
  }),
  '/api/projects/p1/song-file?v=project',
);

// A chapter without an uploaded MP3 also falls back to the project-level MP3.
assert.equal(
  buildSegmentSongAudioUrl({
    segment: { chapterId: 'chap-003' },
    chapters,
    project,
    selectedChapterId: '',
    localChapterSongUrl: '',
    projectSongUrl: '/api/projects/p1/song-file?v=project',
  }),
  '/api/projects/p1/song-file?v=project',
);

assert.equal(
  buildSegmentSongAudioUrl({
    segment: { chapterId: 'chap-001' },
    chapters,
    project: null,
    selectedChapterId: '',
    localChapterSongUrl: '',
    projectSongUrl: '',
  }),
  '',
);
