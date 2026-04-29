const RUNNING_PROGRESS_MAX = 88;

export function advanceAnalysisProgress(currentProgress) {
  const current = Number.isFinite(currentProgress) ? currentProgress : 0;

  if (current >= RUNNING_PROGRESS_MAX) {
    return RUNNING_PROGRESS_MAX;
  }

  if (current < 40) {
    return Math.min(RUNNING_PROGRESS_MAX, current + 12);
  }

  if (current < 70) {
    return Math.min(RUNNING_PROGRESS_MAX, current + 8);
  }

  return Math.min(RUNNING_PROGRESS_MAX, current + 4);
}

export function completeAnalysisProgress() {
  return 100;
}
