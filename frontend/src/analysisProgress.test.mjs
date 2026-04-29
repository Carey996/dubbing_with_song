import assert from 'node:assert/strict';

import { advanceAnalysisProgress, completeAnalysisProgress } from './analysisProgress.js';

assert.equal(advanceAnalysisProgress(0), 12);
assert.equal(advanceAnalysisProgress(84), 88);
assert.equal(advanceAnalysisProgress(88), 88);

assert.equal(completeAnalysisProgress(), 100);
