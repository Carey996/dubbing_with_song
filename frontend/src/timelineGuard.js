export function resolveTimelineReload({
  hasUnsavedTimeline = false,
  localTimeline,
  remoteTimeline,
  discardLocal = false,
  projectChanged = false,
}) {
  if (projectChanged || discardLocal || !hasUnsavedTimeline) {
    return remoteTimeline;
  }
  // Uploads, re-analysis and route re-entry all re-seed the timeline from the server. Keeping
  // the local copy while it is dirty is what stops those flows from silently dropping cue edits.
  return localTimeline;
}

export function isTimelineSaveStale({ revisionAtSave = 0, currentRevision = 0 }) {
  // A PATCH echoes back the payload it received. If the user edited the timeline while that
  // request was in flight, the echo is already behind the local state and must not replace it.
  return revisionAtSave !== currentRevision;
}

export function shouldWarnAboutUnsavedTimeline({ hasUnsavedTimeline = false, nextProjectId = '', currentProjectId = '' }) {
  if (!hasUnsavedTimeline) return false;
  // Opening a different project never loses work from the project being left behind.
  if (nextProjectId && currentProjectId && nextProjectId !== currentProjectId) return false;
  return true;
}
