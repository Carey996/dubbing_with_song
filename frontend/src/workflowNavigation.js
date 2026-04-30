const PAGE_DEFINITIONS = [
  { id: 'input', label: '项目' },
  { id: 'chapters', label: '章节' },
  { id: 'analysis', label: '分析与导出' },
];

export function getWorkflowPages({ project, timeline = { segments: [] } }) {
  const hasProject = Boolean(project);

  return PAGE_DEFINITIONS.filter((page) => {
    if (page.id === 'chapters') return hasProject;
    if (page.id === 'analysis') return hasProject;
    return true;
  });
}

export function normalizeWorkflowPage(pageId, state) {
  if (pageId === 'history') return 'history';
  const pages = getWorkflowPages(state);
  return pages.some((page) => page.id === pageId) ? pageId : 'input';
}

export function getDefaultWorkflowPage({ project, chapters = [], timeline = { segments: [] } }) {
  if (!project) return 'input';
  if (chapters.length > 0) return 'chapters';
  if ((timeline.segments || []).length > 0) return 'analysis';
  return 'input';
}

export function parseWorkflowRoute(pathname = '/') {
  const path = String(pathname).split(/[?#]/)[0] || '/';
  const segments = path
    .split('/')
    .filter(Boolean)
    .map((segment) => decodeURIComponent(segment));

  if (segments.length === 0) {
    return { page: 'input', projectId: '', chapterId: '' };
  }

  if (segments[0] === 'history') {
    return { page: 'history', projectId: '', chapterId: '' };
  }

  if (segments[0] !== 'projects' || !segments[1]) {
    return { page: 'input', projectId: '', chapterId: '' };
  }

  const projectId = segments[1];
  if (segments[2] === 'analysis') {
    return { page: 'analysis', projectId, chapterId: '' };
  }

  if (segments[2] === 'chapters') {
    if (segments[3] && segments[4] === 'analysis') {
      return { page: 'analysis', projectId, chapterId: segments[3] };
    }

    return { page: 'chapters', projectId, chapterId: segments[3] || '' };
  }

  return { page: 'input', projectId, chapterId: '' };
}

export function buildWorkflowPath({ page = 'input', projectId = '', chapterId = '' } = {}) {
  const encodedProjectId = projectId ? encodeURIComponent(projectId) : '';
  const encodedChapterId = chapterId ? encodeURIComponent(chapterId) : '';

  if (page === 'history') return '/history';
  if (!encodedProjectId) return '/';
  if (page === 'chapters') {
    return encodedChapterId
      ? `/projects/${encodedProjectId}/chapters/${encodedChapterId}`
      : `/projects/${encodedProjectId}/chapters`;
  }
  if (page === 'analysis') {
    return encodedChapterId
      ? `/projects/${encodedProjectId}/chapters/${encodedChapterId}/analysis`
      : `/projects/${encodedProjectId}/analysis`;
  }
  return `/projects/${encodedProjectId}`;
}

export function getWorkflowRouteDataNeeds({ page = 'input', projectId = '' } = {}) {
  return {
    projectList: page === 'input' || page === 'history',
    projectDetail: Boolean(projectId),
    chapters: Boolean(projectId) && (page === 'chapters' || page === 'analysis'),
  };
}

export function resolveSelectedChapterIdForRoute({ route, chapters = [], currentChapterId = '' }) {
  if (route?.page === 'analysis' && !route.chapterId) {
    return '';
  }

  if (route?.chapterId && chapters.some((chapter) => chapter.id === route.chapterId)) {
    return route.chapterId;
  }

  if (currentChapterId && chapters.some((chapter) => chapter.id === currentChapterId)) {
    return currentChapterId;
  }

  return chapters[0]?.id || '';
}
