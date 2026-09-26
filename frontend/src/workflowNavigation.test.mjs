import assert from 'node:assert/strict';

import {
  buildWorkflowPath,
  getDefaultWorkflowPage,
  getWorkflowRouteDataNeeds,
  getWorkflowPages,
  normalizeWorkflowPage,
  parseWorkflowRoute,
  resolveChapterSelectionAfterChaptersLoad,
  resolveChaptersProjectSwitch,
  resolveSelectedChapterIdForRoute,
} from './workflowNavigation.js';

const emptyTimeline = { segments: [] };
const analyzedTimeline = { segments: [{ id: 'seg-001' }] };
const routeChapters = [
  { id: 'chap-001', title: '第一章' },
  { id: 'chap-002', title: '第二章' },
];

assert.deepEqual(
  getWorkflowPages({ project: null, chapters: [], timeline: emptyTimeline }).map((page) => page.id),
  ['input'],
);

assert.deepEqual(
  getWorkflowPages({ project: { id: 'p1' }, chapters: [{ id: 'chap-001' }], timeline: emptyTimeline }).map((page) => page.id),
  ['input', 'chapters', 'analysis'],
);

assert.deepEqual(
  getWorkflowPages({ project: { id: 'p1' }, chapters: [], timeline: analyzedTimeline }).map((page) => page.id),
  ['input', 'chapters', 'analysis'],
);

assert.equal(normalizeWorkflowPage('analysis', { project: null, chapters: [], timeline: emptyTimeline }), 'input');
assert.equal(normalizeWorkflowPage('render', { project: { id: 'p1' }, chapters: [], timeline: emptyTimeline }), 'input');
assert.equal(normalizeWorkflowPage('history', { project: null, chapters: [], timeline: emptyTimeline }), 'history');
assert.equal(normalizeWorkflowPage('render', { project: { id: 'p1' }, chapters: [], timeline: analyzedTimeline }), 'input');

assert.equal(getDefaultWorkflowPage({ project: null, chapters: [], timeline: emptyTimeline }), 'input');
assert.equal(getDefaultWorkflowPage({ project: { id: 'p1' }, chapters: [{ id: 'chap-001' }], timeline: emptyTimeline }), 'chapters');
assert.equal(getDefaultWorkflowPage({ project: { id: 'p1' }, chapters: [], timeline: analyzedTimeline }), 'analysis');

assert.deepEqual(parseWorkflowRoute('/'), { page: 'input', projectId: '', chapterId: '' });
assert.deepEqual(parseWorkflowRoute('/history'), { page: 'history', projectId: '', chapterId: '' });
assert.deepEqual(parseWorkflowRoute('/projects/p1'), { page: 'input', projectId: 'p1', chapterId: '' });
assert.deepEqual(parseWorkflowRoute('/projects/p1/chapters'), { page: 'chapters', projectId: 'p1', chapterId: '' });
assert.deepEqual(parseWorkflowRoute('/projects/p1/chapters/chap-001'), { page: 'chapters', projectId: 'p1', chapterId: 'chap-001' });
assert.deepEqual(parseWorkflowRoute('/projects/p1/analysis'), { page: 'analysis', projectId: 'p1', chapterId: '' });
assert.deepEqual(parseWorkflowRoute('/projects/p1/chapters/chap-001/analysis'), { page: 'analysis', projectId: 'p1', chapterId: 'chap-001' });
assert.deepEqual(parseWorkflowRoute('/projects/p%201/chapters/%E7%AC%AC%E4%B8%80%E7%AB%A0/analysis?from=nav'), {
  page: 'analysis',
  projectId: 'p 1',
  chapterId: '第一章',
});
assert.deepEqual(parseWorkflowRoute('/unknown/path'), { page: 'input', projectId: '', chapterId: '' });

assert.equal(buildWorkflowPath({ page: 'input' }), '/');
assert.equal(buildWorkflowPath({ page: 'history' }), '/history');
assert.equal(buildWorkflowPath({ page: 'input', projectId: 'p1' }), '/projects/p1');
assert.equal(buildWorkflowPath({ page: 'chapters', projectId: 'p1' }), '/projects/p1/chapters');
assert.equal(buildWorkflowPath({ page: 'chapters', projectId: 'p1', chapterId: 'chap-001' }), '/projects/p1/chapters/chap-001');
assert.equal(buildWorkflowPath({ page: 'analysis', projectId: 'p1' }), '/projects/p1/analysis');
assert.equal(buildWorkflowPath({ page: 'analysis', projectId: 'p1', chapterId: 'chap-001' }), '/projects/p1/chapters/chap-001/analysis');
assert.equal(buildWorkflowPath({ page: 'analysis', projectId: 'p 1', chapterId: '第一章' }), '/projects/p%201/chapters/%E7%AC%AC%E4%B8%80%E7%AB%A0/analysis');

assert.deepEqual(getWorkflowRouteDataNeeds({ page: 'input', projectId: '', chapterId: '' }), {
  projectList: true,
  projectDetail: false,
  chapters: false,
});
assert.deepEqual(getWorkflowRouteDataNeeds({ page: 'history', projectId: '', chapterId: '' }), {
  projectList: true,
  projectDetail: false,
  chapters: false,
});
assert.deepEqual(getWorkflowRouteDataNeeds({ page: 'input', projectId: 'p1', chapterId: '' }), {
  projectList: true,
  projectDetail: true,
  chapters: false,
});
assert.deepEqual(getWorkflowRouteDataNeeds({ page: 'chapters', projectId: 'p1', chapterId: '' }), {
  projectList: false,
  projectDetail: true,
  chapters: true,
});
assert.deepEqual(getWorkflowRouteDataNeeds({ page: 'analysis', projectId: 'p1', chapterId: 'chap-001' }), {
  projectList: false,
  projectDetail: true,
  chapters: true,
});

assert.equal(resolveSelectedChapterIdForRoute({
  route: { page: 'analysis', projectId: 'p1', chapterId: '' },
  chapters: routeChapters,
  currentChapterId: 'chap-001',
  currentScopeProjectId: 'p1',
}), '');
assert.equal(resolveSelectedChapterIdForRoute({
  route: { page: 'analysis', projectId: 'p1', chapterId: 'chap-002' },
  chapters: routeChapters,
  currentChapterId: '',
  currentScopeProjectId: '',
}), 'chap-002');
assert.equal(resolveSelectedChapterIdForRoute({
  route: { page: 'chapters', projectId: 'p1', chapterId: '' },
  chapters: routeChapters,
  currentChapterId: '',
  currentScopeProjectId: 'p1',
}), '', 'the chapters route carries no chapter, so the route must not invent one');
assert.equal(resolveSelectedChapterIdForRoute({
  route: { page: 'chapters', projectId: 'p1', chapterId: 'missing' },
  chapters: routeChapters,
  currentChapterId: 'chap-002',
  currentScopeProjectId: 'p1',
}), 'chap-002');

// Chapter ids are per-index (chap-001, chap-002, ...) and identical in every project, so a
// chapter selected in one project must never be applied to another project.
assert.equal(resolveSelectedChapterIdForRoute({
  route: { page: 'chapters', projectId: 'p2', chapterId: '' },
  chapters: routeChapters,
  currentChapterId: 'chap-003',
  currentScopeProjectId: 'p1',
}), '');
assert.equal(resolveSelectedChapterIdForRoute({
  route: { page: 'analysis', projectId: 'p2', chapterId: '' },
  chapters: routeChapters,
  currentChapterId: 'chap-002',
  currentScopeProjectId: 'p1',
}), '', 'a whole-project analysis route stays whole-project even right after a project switch');

// Chapter ids are per-index (chap-001, chap-002, ...) and identical in every project, so a
// chapter selected in one project is dropped when the open project changes.
assert.equal(resolveChaptersProjectSwitch({
  nextProjectId: 'p2',
  currentScopeProjectId: 'p1',
  currentChapterId: 'chap-003',
}), '');
assert.equal(resolveChaptersProjectSwitch({
  nextProjectId: 'p1',
  currentScopeProjectId: 'p1',
  currentChapterId: 'chap-003',
}), 'chap-003');
assert.equal(resolveChaptersProjectSwitch({
  nextProjectId: 'p2',
  currentScopeProjectId: '',
  currentChapterId: '',
}), '');

// App.jsx applyChapters() captures the scope it is leaving and passes it here as
// previousProjectId. Reading that scope ref after moving it made every project switch look like
// a reload of the same project, so chap-002 stayed selected in the newly opened project.
assert.equal(resolveChapterSelectionAfterChaptersLoad({
  previousProjectId: 'p1',
  nextProjectId: 'p2',
  currentChapterId: 'chap-002',
  chapters: routeChapters,
}), '', 'a project switch must drop the previous project chapter');
assert.equal(resolveChapterSelectionAfterChaptersLoad({
  previousProjectId: 'p2',
  nextProjectId: 'p2',
  currentChapterId: 'chap-002',
  chapters: routeChapters,
}), 'chap-002', 'reloading the same project keeps the chapter in scope');
assert.equal(resolveChapterSelectionAfterChaptersLoad({
  previousProjectId: '',
  nextProjectId: 'p2',
  currentChapterId: 'chap-001',
  chapters: routeChapters,
}), '', 'a freshly created project never inherits a chapter selection');
assert.equal(resolveChapterSelectionAfterChaptersLoad({
  previousProjectId: 'p2',
  nextProjectId: 'p2',
  currentChapterId: 'chap-999',
  chapters: routeChapters,
}), '', 'a chapter id that no longer exists is dropped');
