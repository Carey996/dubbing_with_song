import assert from 'node:assert/strict';

import {
  areAllProjectsSelected,
  normalizeSelectedProjectIds,
  selectAllProjectIds,
  toggleProjectSelection,
} from './projectSelection.js';

const projects = [{ id: 'first' }, { id: 'second' }, { id: 'third' }];

assert.deepEqual(selectAllProjectIds(projects), ['first', 'second', 'third']);
assert.deepEqual(toggleProjectSelection(['first'], 'second'), ['first', 'second']);
assert.deepEqual(toggleProjectSelection(['first', 'second'], 'first'), ['second']);
assert.deepEqual(normalizeSelectedProjectIds(projects, ['missing', 'third']), ['third']);
assert.equal(areAllProjectsSelected(projects, ['first', 'second', 'third']), true);
assert.equal(areAllProjectsSelected(projects, ['first']), false);
assert.equal(areAllProjectsSelected([], []), false);
