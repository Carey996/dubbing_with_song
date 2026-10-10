const NUMBER_TYPES = new Set(['number']);

const RESULT_ROWS = {
  'project-intake': [
    ['chapterCount', '章节数'],
    ['textLength', '字数'],
  ],
  'chapter-split': [
    ['chapterCount', '章节数'],
    ['textLength', '总字数'],
    ['longestChapter', '最长章节字数'],
  ],
  'content-analysis': [
    ['segmentCount', '片段数'],
    ['lyricCount', '歌词片段'],
    ['songCandidateCount', '歌曲候选'],
    ['chapterCount', '章节数'],
  ],
  'chapter-analysis': [
    ['chapterTitle', '章节'],
    ['chapterSegmentCount', '本章片段'],
    ['chapterLyricCount', '本章歌词片段'],
    ['chapterLrcAlignedCount', 'LRC 对齐片段'],
    ['projectSegmentCount', '项目片段总数'],
  ],
  'song-alignment': [
    ['matchedCount', '已匹配歌词片段'],
    ['lyricSegmentCount', '歌词片段总数'],
    ['needsReviewCount', '待人工确认'],
    ['lrcLineCount', 'LRC 行数'],
    ['songFilename', '音源文件'],
  ],
  'timeline-edit': [
    ['segmentCount', '片段数'],
    ['lyricCount', '歌词片段'],
    ['totalDurationSec', '总时长（秒）'],
    ['bgmVolume', 'BGM 音量'],
    ['narrationVolume', '旁白音量'],
    ['songStartSec', '歌曲起点（秒）'],
  ],
  render: [
    ['status', '渲染状态'],
    ['message', '渲染结果'],
    ['renderId', '渲染记录'],
    ['savedAt', '保存时间'],
  ],
};

export function normalizeWorkflowCatalog(payload) {
  const workflows = Array.isArray(payload) ? payload : payload?.workflows;
  if (!Array.isArray(workflows)) return [];
  return workflows
    .filter((item) => item && typeof item.id === 'string' && item.id)
    .map((item) => ({
      id: item.id,
      name: item.name || item.id,
      group: item.group || '其他',
      order: Number.isFinite(Number(item.order)) ? Number(item.order) : 0,
      description: item.description || '',
      requiresLlm: Boolean(item.requiresLlm),
      requiresTts: Boolean(item.requiresTts),
      requiresFfmpeg: Boolean(item.requiresFfmpeg),
      fields: Array.isArray(item.fields) ? item.fields.map(normalizeField).filter(Boolean) : [],
    }))
    .sort((left, right) => left.order - right.order);
}

function normalizeField(field) {
  if (!field || typeof field.name !== 'string' || !field.name) return null;
  return {
    name: field.name,
    label: field.label || field.name,
    type: field.type || 'text',
    required: Boolean(field.required),
    default: field.default ?? null,
    help: field.help || '',
    placeholder: field.placeholder || '',
    dependsOn: field.dependsOn || '',
    expects: field.expects || '',
  };
}

export function groupWorkflows(workflows = []) {
  const groups = [];
  const byName = new Map();
  for (const workflow of workflows) {
    if (!byName.has(workflow.group)) {
      const entry = { group: workflow.group, workflows: [] };
      byName.set(workflow.group, entry);
      groups.push(entry);
    }
    byName.get(workflow.group).workflows.push(workflow);
  }
  return groups;
}

export function findWorkflow(workflows = [], workflowId = '') {
  return workflows.find((workflow) => workflow.id === workflowId) || null;
}

export function defaultFormValues(workflow) {
  const values = {};
  for (const field of workflow?.fields || []) {
    values[field.name] = field.default === null || field.default === undefined ? '' : String(field.default);
  }
  return values;
}

export function validateFormValues(workflow, values = {}) {
  const errors = {};
  for (const field of workflow?.fields || []) {
    const raw = values[field.name];
    const text = typeof raw === 'string' ? raw.trim() : raw === undefined || raw === null ? '' : String(raw);
    if (!text) {
      if (field.required) errors[field.name] = `请填写${field.label}`;
      continue;
    }
    if (NUMBER_TYPES.has(field.type) && !Number.isFinite(Number(text))) {
      errors[field.name] = `${field.label}必须是数字`;
      continue;
    }
    if (field.type === 'json') {
      let parsed;
      try {
        parsed = JSON.parse(text);
      } catch {
        errors[field.name] = `${field.label}不是合法 JSON`;
        continue;
      }
      if (field.expects === 'list' && !Array.isArray(parsed)) {
        errors[field.name] = `${field.label}必须是 JSON 数组`;
      }
    }
  }
  return { ok: Object.keys(errors).length === 0, errors };
}

export function buildRunPayload(workflow, values = {}) {
  const payload = {};
  for (const field of workflow?.fields || []) {
    const raw = values[field.name];
    const text = typeof raw === 'string' ? raw.trim() : raw === undefined || raw === null ? '' : String(raw);
    if (!text) continue;
    if (NUMBER_TYPES.has(field.type)) {
      payload[field.name] = Number(text);
      continue;
    }
    if (field.type === 'json') {
      payload[field.name] = JSON.parse(text);
      continue;
    }
    payload[field.name] = text;
  }
  return payload;
}

export function summarizeRunResult(workflowId, result) {
  if (!result || typeof result !== 'object') {
    return { headline: '工作流已完成。', rows: [], links: [], audioUrl: '' };
  }
  const rows = (RESULT_ROWS[workflowId] || [])
    .filter(([key]) => result[key] !== undefined && result[key] !== null && result[key] !== '')
    .map(([key, label]) => ({ label, value: formatValue(result[key]) }));
  const links = [];
  if (result.projectPath) links.push({ label: '打开章节页', href: result.projectPath });
  if (result.analysisPath) links.push({ label: '打开分析页', href: result.analysisPath });
  return {
    headline: result.message || result.title || '工作流已完成。',
    rows,
    links,
    audioUrl: typeof result.outputUrl === 'string' ? result.outputUrl : '',
    warnings: Array.isArray(result.warnings) ? result.warnings : [],
  };
}

export function summarizeRunFailure(snapshot) {
  const payload = snapshot?.payload || {};
  const message = snapshot?.error || payload.error || '工作流执行失败。';
  return {
    message,
    code: snapshot?.errorCode || payload.errorCode || '',
    node: snapshot?.failedNode || payload.node || '',
  };
}

export function describeNodeStatus(status) {
  if (status === 'running') return '执行中';
  if (status === 'succeeded') return '完成';
  if (status === 'skipped') return '跳过';
  if (status === 'failed') return '失败';
  return status || '等待';
}

export function formatDuration(elapsedMs) {
  // Number(null) is 0, so an absent duration must be rejected before the numeric coercion.
  if (elapsedMs === null || elapsedMs === undefined || elapsedMs === '') return '';
  if (!Number.isFinite(Number(elapsedMs))) return '';
  const value = Number(elapsedMs);
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(1)} s`;
}

export function buildTimelineSegmentsJson(projectDetail) {
  const segments = projectDetail?.timeline?.segments;
  if (!Array.isArray(segments)) return '';
  return JSON.stringify(segments, null, 2);
}

function formatValue(value) {
  if (typeof value === 'number') return String(Math.round(value * 1000) / 1000);
  if (typeof value === 'boolean') return value ? '是' : '否';
  return String(value);
}
