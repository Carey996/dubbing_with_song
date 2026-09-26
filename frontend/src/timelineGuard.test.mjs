import assert from 'node:assert/strict';

import {
  isTimelineSaveStale,
  resolveTimelineReload,
  shouldWarnAboutUnsavedTimeline,
} from './timelineGuard.js';

const localTimeline = { bgmVolume: 0.9, segments: [{ id: 'seg-001', durationSec: 9.5 }] };
const remoteTimeline = { bgmVolume: 0.22, segments: [{ id: 'seg-001', durationSec: 3 }] };

// Clean state: the server is the source of truth.
assert.equal(resolveTimelineReload({ hasUnsavedTimeline: false, localTimeline, remoteTimeline }), remoteTimeline);

// Unsaved edits must survive a reload triggered by an upload or by re-entering the route.
assert.equal(resolveTimelineReload({ hasUnsavedTimeline: true, localTimeline, remoteTimeline }), localTimeline);

// An explicit "discard my edits" action must still be able to take the server copy.
assert.equal(
  resolveTimelineReload({ hasUnsavedTimeline: true, localTimeline, remoteTimeline, discardLocal: true }),
  remoteTimeline,
);

// A different project always replaces the timeline, dirty or not.
assert.equal(
  resolveTimelineReload({ hasUnsavedTimeline: true, localTimeline, remoteTimeline, projectChanged: true }),
  remoteTimeline,
);

// Switching between projects must not be blocked by another project's unsaved edits.
assert.equal(shouldWarnAboutUnsavedTimeline({ hasUnsavedTimeline: true, nextProjectId: 'p2', currentProjectId: 'p1' }), false);
assert.equal(shouldWarnAboutUnsavedTimeline({ hasUnsavedTimeline: true, nextProjectId: 'p1', currentProjectId: 'p1' }), true);
assert.equal(shouldWarnAboutUnsavedTimeline({ hasUnsavedTimeline: false, nextProjectId: 'p1', currentProjectId: 'p1' }), false);
// Entering a project from the history page has no current project yet.
assert.equal(shouldWarnAboutUnsavedTimeline({ hasUnsavedTimeline: true, nextProjectId: 'p1', currentProjectId: '' }), true);

// A PATCH echoes the payload it received. An edit made while that request was in flight makes
// the echo stale: applying it would drop the edit and a render started from it would be wrong.
assert.equal(isTimelineSaveStale({ revisionAtSave: 3, currentRevision: 3 }), false);
assert.equal(isTimelineSaveStale({ revisionAtSave: 3, currentRevision: 4 }), true);
assert.equal(isTimelineSaveStale({}), false, 'no edits means the response is never stale');
