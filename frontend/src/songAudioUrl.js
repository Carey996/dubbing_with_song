export function buildProjectSongAudioUrl(project) {
  if (!project?.hasSong) return '';
  return `/api/projects/${project.id}/song-file?v=${assetVersion(project)}`;
}

export function buildSelectedChapterSongAudioUrl(project, selectedChapter) {
  if (!project || !selectedChapter?.chapterSong) return '';
  const url = selectedChapter.chapterSong.url
    || `/api/projects/${project.id}/chapters/${selectedChapter.id}/song-file`;
  return `${url}?v=${encodeURIComponent(selectedChapter.chapterSong.updatedAt || assetVersion(project))}`;
}

export function buildSegmentSongAudioUrl({
  segment,
  chapters = [],
  project,
  selectedChapterId = '',
  localChapterSongUrl = '',
  projectSongUrl = '',
}) {
  const segmentChapter = chapters.find((chapter) => chapter.id === segment?.chapterId);
  if (segmentChapter?.id && segmentChapter.id === selectedChapterId && localChapterSongUrl) {
    return localChapterSongUrl;
  }
  if (project && segmentChapter?.chapterSong) {
    const url = segmentChapter.chapterSong.url
      || `/api/projects/${project.id}/chapters/${segmentChapter.id}/song-file`;
    // Chapter MP3 re-uploads keep the same URL, so the cache buster has to change with the asset.
    const version = segmentChapter.chapterSong.updatedAt || assetVersion(project);
    return `${url}?v=${encodeURIComponent(version)}`;
  }
  return projectSongUrl;
}

function assetVersion(project) {
  return encodeURIComponent(project?.updatedAt || project?.id || '');
}
