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

export function shouldWarnAboutUnsavedTimeline({ hasUnsavedTimeline = false, nextProjectId = '', currentProjectId = '' }) {
  if (!hasUnsavedTimeline) return false;
  // Opening a different project never loses work from the project being left behind.
  if (nextProjectId && currentProjectId && nextProjectId !== currentProjectId) return false;
  return true;
}
