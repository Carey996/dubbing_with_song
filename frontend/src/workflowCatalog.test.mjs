import assert from 'node:assert/strict';

import {
  buildRunPayload,
  buildTimelineSegmentsJson,
  defaultFormValues,
  describeNodeStatus,
  findWorkflow,
  formatDuration,
  groupWorkflows,
  normalizeWorkflowCatalog,
  summarizeRunFailure,
  summarizeRunResult,
  validateFormValues,
} from './workflowCatalog.js';

const catalogPayload = {
  workflows: [
    {
      id: 'render',
      name: '渲染导出',
      group: '时间轴与渲染',
      order: 7,
      description: '渲染。',
      requiresTts: true,
      fields: [{ name: 'projectId', label: '项目', type: 'project', required: true }],
    },
    {
      id: 'content-analysis',
      name: '全篇内容分析',
      group: '内容分析',
      order: 3,
      description: '分析。',
      requiresLlm: true,
      fields: [
        { name: 'projectId', label: '项目', type: 'project', required: true },
        { name: 'chunkChars', label: '块字数', type: 'number', help: '默认 1200' },
      ],
    },
    { name: 'missing id gets dropped', group: 'x', fields: [] },
  ],
};

const catalog = normalizeWorkflowCatalog(catalogPayload);

assert.deepEqual(catalog.map((item) => item.id), ['content-analysis', 'render'], 'catalogue is sorted by order');
assert.equal(catalog[0].requiresLlm, true);
assert.equal(catalog[1].requiresTts, true);
assert.equal(catalog[0].fields[1].type, 'number');
assert.deepEqual(normalizeWorkflowCatalog(null), []);
assert.deepEqual(normalizeWorkflowCatalog({ workflows: 'nope' }), []);

assert.deepEqual(
  groupWorkflows(catalog).map((group) => [group.group, group.workflows.length]),
  [['内容分析', 1], ['时间轴与渲染', 1]],
);

assert.equal(findWorkflow(catalog, 'render').name, '渲染导出');
assert.equal(findWorkflow(catalog, 'nope'), null);

assert.deepEqual(defaultFormValues(catalog[0]), { projectId: '', chunkChars: '' });
assert.deepEqual(
  defaultFormValues({ fields: [{ name: 'bgmVolume', type: 'number', default: 0.22 }] }),
  { bgmVolume: '0.22' },
);

// required fields
assert.deepEqual(validateFormValues(catalog[0], {}), {
  ok: false,
  errors: { projectId: '请填写项目' },
});
assert.equal(validateFormValues(catalog[0], { projectId: 'p1' }).ok, true);
assert.equal(validateFormValues(catalog[0], { projectId: 'p1', chunkChars: 'abc' }).errors.chunkChars, '块字数必须是数字');
assert.equal(validateFormValues(catalog[0], { projectId: 'p1', chunkChars: '900' }).ok, true);

const timelineWorkflow = {
  fields: [
    { name: 'projectId', type: 'project', required: true },
    { name: 'segments', label: '片段', type: 'json', required: true, expects: 'list' },
  ],
};
assert.equal(validateFormValues(timelineWorkflow, { projectId: 'p1', segments: '[{' }).errors.segments, '片段不是合法 JSON');
assert.equal(
  validateFormValues(timelineWorkflow, { projectId: 'p1', segments: '{"a":1}' }).errors.segments,
  '片段必须是 JSON 数组',
);
assert.equal(validateFormValues(timelineWorkflow, { projectId: 'p1', segments: '[{"id":"s1"}]' }).ok, true);

assert.deepEqual(buildRunPayload(catalog[0], { projectId: 'p1', chunkChars: '900', ignored: 'x' }), {
  projectId: 'p1',
  chunkChars: 900,
});
assert.deepEqual(
  buildRunPayload(timelineWorkflow, { projectId: 'p1', segments: '[{"id":"s1"}]' }),
  { projectId: 'p1', segments: [{ id: 's1' }] },
);
assert.deepEqual(buildRunPayload(catalog[0], { projectId: 'p1', chunkChars: '' }), { projectId: 'p1' });

assert.deepEqual(summarizeRunResult('render', { status: 'mixed', message: '混音完成', renderId: 'r1', outputUrl: '/outputs/p1/r1/mixed.mp3' }), {
  headline: '混音完成',
  rows: [
    { label: '渲染状态', value: 'mixed' },
    { label: '渲染结果', value: '混音完成' },
    { label: '渲染记录', value: 'r1' },
  ],
  links: [],
  audioUrl: '/outputs/p1/r1/mixed.mp3',
  warnings: [],
});

const analysisSummary = summarizeRunResult('content-analysis', {
  segmentCount: 12,
  lyricCount: 3,
  songCandidateCount: 2,
  analysisPath: '/projects/p1/analysis',
});
assert.equal(analysisSummary.headline, '工作流已完成。');
assert.equal(analysisSummary.rows.length, 3);
assert.deepEqual(analysisSummary.links, [{ label: '打开分析页', href: '/projects/p1/analysis' }]);

assert.deepEqual(summarizeRunResult('render', null), { headline: '工作流已完成。', rows: [], links: [], audioUrl: '' });

assert.deepEqual(
  summarizeRunFailure({ status: 'failed', error: '缺少 TTS_API_KEY', errorCode: 'workflow_failed', failedNode: 'build_narration' }),
  { message: '缺少 TTS_API_KEY', code: 'workflow_failed', node: 'build_narration' },
);
assert.deepEqual(
  summarizeRunFailure({ status: 'failed', payload: { error: 'payload 错误', errorCode: 'x', node: 'n' } }),
  { message: 'payload 错误', code: 'x', node: 'n' },
);

assert.equal(describeNodeStatus('running'), '执行中');
assert.equal(describeNodeStatus('skipped'), '跳过');
assert.equal(describeNodeStatus('mystery'), 'mystery');
assert.equal(formatDuration(420), '420 ms');
assert.equal(formatDuration(1500), '1.5 s');
assert.equal(formatDuration(null), '');

assert.equal(buildTimelineSegmentsJson({ timeline: { segments: [{ id: 'seg-001' }] } }), '[\n  {\n    "id": "seg-001"\n  }\n]');
assert.equal(buildTimelineSegmentsJson({ timeline: {} }), '');
assert.equal(buildTimelineSegmentsJson(null), '');

console.log('workflowCatalog tests passed');
