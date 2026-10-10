import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Download,
  Loader2,
  MinusCircle,
  Play,
  RefreshCw,
  Sparkles,
  Workflow as WorkflowIcon,
  XCircle,
} from 'lucide-react';
import { request } from './api.js';
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
import {
  applyRunEvent,
  createRunState,
  followRunEvents,
  isRunTerminal,
  runStateFromSnapshot,
} from './workflowRunStream.js';

const REQUIREMENT_LABELS = {
  requiresLlm: '需要 LLM',
  requiresTts: '需要 TTS',
  requiresFfmpeg: '需要 ffmpeg',
};

export default function WorkflowPage() {
  const [workflows, setWorkflows] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [values, setValues] = useState({});
  const [fieldErrors, setFieldErrors] = useState({});
  const [projects, setProjects] = useState([]);
  const [chapters, setChapters] = useState([]);
  const [runs, setRuns] = useState([]);
  const [runState, setRunState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const runStateRef = useRef(null);
  const streamAbortRef = useRef(null);

  const workflow = useMemo(() => findWorkflow(workflows, selectedId), [workflows, selectedId]);
  const groups = useMemo(() => groupWorkflows(workflows), [workflows]);
  const projectIdValue = values.projectId || '';
  const summary = runState?.result ? summarizeRunResult(runState.workflowId, runState.result) : null;

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError('');
      try {
        const [catalog, projectList, runList] = await Promise.all([
          request('/api/workflows'),
          request('/api/projects'),
          request('/api/workflows/runs?limit=20'),
        ]);
        if (cancelled) return;
        const normalized = normalizeWorkflowCatalog(catalog);
        setWorkflows(normalized);
        setProjects(projectList.projects || []);
        setRuns(runList.runs || []);
        setSelectedId((current) => (findWorkflow(normalized, current) ? current : normalized[0]?.id || ''));
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!workflow) return;
    setValues(defaultFormValues(workflow));
    setFieldErrors({});
  }, [workflow?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false;
    if (!projectIdValue) {
      setChapters([]);
      return undefined;
    }
    request(`/api/projects/${encodeURIComponent(projectIdValue)}/chapters`)
      .then((data) => {
        if (!cancelled) setChapters(data.chapters || []);
      })
      .catch(() => {
        if (!cancelled) setChapters([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectIdValue]);

  useEffect(() => () => streamAbortRef.current?.abort(), []);

  function selectWorkflow(nextId) {
    streamAbortRef.current?.abort();
    setSelectedId(nextId);
    setRunState(null);
    runStateRef.current = null;
    setError('');
  }

  function updateValue(name, value) {
    setValues((current) => ({ ...current, [name]: value }));
    setFieldErrors((current) => (current[name] ? { ...current, [name]: '' } : current));
  }

  async function refreshRuns() {
    try {
      const data = await request('/api/workflows/runs?limit=20');
      setRuns(data.runs || []);
    } catch {
      // run history is secondary: a listing failure must not hide the run result
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!workflow || running) return;

    const validation = validateFormValues(workflow, values);
    setFieldErrors(validation.errors);
    if (!validation.ok) return;

    setError('');
    setRunning(true);
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    try {
      const created = await request(`/api/workflows/${encodeURIComponent(workflow.id)}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildRunPayload(workflow, values)),
      });
      await followRun(created.runId, controller);
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
      await refreshRuns();
    }
  }

  async function followRun(runId, controller) {
    const initial = createRunState({ runId, workflowId: workflow?.id || '' });
    runStateRef.current = initial;
    setRunState(initial);

    await followRunEvents({
      runId,
      signal: controller.signal,
      readLastSeq: () => runStateRef.current?.lastSeq || 0,
      isTerminal: () => isRunTerminal(runStateRef.current?.status),
      onEvent: (streamEvent) => {
        const next = applyRunEvent(runStateRef.current || initial, streamEvent);
        runStateRef.current = next;
        setRunState(next);
      },
    });
  }

  async function openRun(runId) {
    if (!runId) return;
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;
    setRunning(true);
    setError('');
    try {
      const snapshot = await request(`/api/workflows/runs/${encodeURIComponent(runId)}`);
      const restored = runStateFromSnapshot(snapshot);
      runStateRef.current = restored;
      setRunState(restored);
      if (snapshot.workflowId) setSelectedId(snapshot.workflowId);
      if (isRunTerminal(restored.status)) return;
      await followRunEvents({
        runId,
        signal: controller.signal,
        readLastSeq: () => runStateRef.current?.lastSeq || 0,
        isTerminal: () => isRunTerminal(runStateRef.current?.status),
        onEvent: (streamEvent) => {
          const next = applyRunEvent(runStateRef.current || restored, streamEvent);
          runStateRef.current = next;
          setRunState(next);
        },
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
      await refreshRuns();
    }
  }

  async function prefillTimeline() {
    if (!projectIdValue) {
      setError('请先选择项目，再载入当前时间轴。');
      return;
    }
    try {
      const detail = await request(`/api/projects/${encodeURIComponent(projectIdValue)}`);
      const text = buildTimelineSegmentsJson(detail);
      if (!text) {
        setError('该项目还没有保存的时间轴片段。');
        return;
      }
      updateValue('segments', text);
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section className="workflow-page">
      <aside className="workflow-catalog panel">
        <div className="panel-title">
          <WorkflowIcon size={18} />
          <h2>工作流 Agent</h2>
        </div>
        <p className="workflow-catalog-hint">每个流程都是一个 LangGraph 工作流，可单独运行并逐步查看节点状态。</p>
        {loading && <p className="workflow-muted"><Loader2 className="spin" size={16} /> 正在加载工作流目录…</p>}
        {groups.map((group) => (
          <div key={group.group} className="workflow-group">
            <h3>{group.group}</h3>
            <ul>
              {group.workflows.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`workflow-pick ${item.id === selectedId ? 'primary' : ''}`}
                    onClick={() => selectWorkflow(item.id)}
                  >
                    <span className="workflow-pick-name">{item.name}</span>
                    <span className="workflow-pick-id">{item.id}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </aside>

      <div className="workflow-main">
        {error && <div className="notice error">{error}</div>}

        {workflow && (
          <article className="panel workflow-runner">
            <div className="panel-title">
              <Sparkles size={18} />
              <h2>{workflow.name}</h2>
            </div>
            <p className="workflow-description">{workflow.description}</p>
            <div className="workflow-requirements">
              {Object.entries(REQUIREMENT_LABELS)
                .filter(([key]) => workflow[key])
                .map(([key, label]) => (
                  <span key={key} className="workflow-requirement">{label}</span>
                ))}
            </div>

            <form className="workflow-form" onSubmit={handleSubmit}>
              {workflow.fields.map((field) => (
                <WorkflowFieldInput
                  key={field.name}
                  field={field}
                  value={values[field.name] ?? ''}
                  error={fieldErrors[field.name]}
                  projects={projects}
                  chapters={chapters}
                  canPrefillTimeline={field.name === 'segments' && Boolean(projectIdValue)}
                  onPrefillTimeline={prefillTimeline}
                  onChange={(value) => updateValue(field.name, value)}
                />
              ))}
              <button type="submit" className="primary workflow-run-button" disabled={running}>
                {running ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
                {running ? '运行中…' : '运行工作流'}
              </button>
            </form>
          </article>
        )}

        {runState && <RunTimeline runState={runState} />}
        {summary && <RunResult summary={summary} runState={runState} />}

        <RunHistory runs={runs} activeRunId={runState?.runId || ''} onOpen={openRun} onRefresh={refreshRuns} />
      </div>
    </section>
  );
}

function WorkflowFieldInput({
  field,
  value,
  error,
  projects,
  chapters,
  canPrefillTimeline,
  onPrefillTimeline,
  onChange,
}) {
  const inputId = `workflow-field-${field.name}`;
  const help = error || field.help;

  return (
    <label className={`workflow-field ${error ? 'has-error' : ''}`} htmlFor={inputId}>
      <span className="workflow-field-label">
        {field.label}
        {field.required && <em>必填</em>}
      </span>

      {field.type === 'textarea' && (
        <textarea
          id={inputId}
          rows={field.name === 'lrcText' ? 6 : 8}
          value={value}
          placeholder={field.placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      )}

      {field.type === 'json' && (
        <textarea
          id={inputId}
          rows={10}
          value={value}
          spellCheck={false}
          placeholder='[{"id": "seg-001", "type": "narration", "text": "…", "durationSec": 3}]'
          onChange={(event) => onChange(event.target.value)}
        />
      )}

      {field.type === 'number' && (
        <input
          id={inputId}
          type="number"
          step="any"
          value={value}
          placeholder={field.placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      )}

      {field.type === 'project' && (
        <select id={inputId} value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">请选择项目…</option>
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.title}（{project.id}）
            </option>
          ))}
        </select>
      )}

      {field.type === 'chapter' && (
        <select
          id={inputId}
          value={value}
          disabled={!chapters.length}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">{chapters.length ? '请选择章节…' : '请先选择项目…'}</option>
          {chapters.map((chapter) => (
            <option key={chapter.id} value={chapter.id}>
              {chapter.title}（{chapter.id}）
            </option>
          ))}
        </select>
      )}

      {(field.type === 'text' || !field.type) && (
        <input
          id={inputId}
          type="text"
          value={value}
          placeholder={field.placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      )}

      {canPrefillTimeline && (
        <button type="button" className="workflow-inline-action" onClick={onPrefillTimeline}>
          <Download size={14} />
          载入当前时间轴
        </button>
      )}

      {help && <span className="workflow-field-help">{help}</span>}
    </label>
  );
}

function RunTimeline({ runState }) {
  const failed = summarizeRunFailure(runState);

  return (
    <article className="panel workflow-timeline-panel">
      <div className="panel-title">
        <CircleDashed size={18} />
        <h2>执行进度</h2>
        <span className={`workflow-run-status ${runState.status}`}>{statusLabel(runState.status)}</span>
      </div>

      {runState.steps.length === 0 && <p className="workflow-muted">等待第一个节点开始…</p>}

      <ol className="workflow-timeline">
        {runState.steps.map((step) => (
          <li key={step.id} className={`workflow-timeline-item ${step.status}`}>
            <span className="workflow-timeline-icon">{statusIcon(step.status)}</span>
            <div className="workflow-timeline-body">
              <div className="workflow-timeline-head">
                <strong>{step.label || step.node}</strong>
                <span className="workflow-timeline-meta">
                  {describeNodeStatus(step.status)}
                  {step.elapsedMs !== null && step.elapsedMs !== undefined ? ` · ${formatDuration(step.elapsedMs)}` : ''}
                </span>
              </div>
              {step.detail && <p className="workflow-timeline-detail">{step.detail}</p>}
            </div>
          </li>
        ))}
      </ol>

      {runState.warnings.length > 0 && (
        <ul className="workflow-warnings">
          {runState.warnings.map((warning) => (
            <li key={warning}>
              <AlertTriangle size={14} />
              {warning}
            </li>
          ))}
        </ul>
      )}

      {runState.status === 'failed' && (
        <div className="notice error workflow-failure">
          <strong>工作流失败{runState.failedNode ? `（节点：${runState.failedNode}）` : ''}</strong>
          <p>{failed.message}</p>
          {failed.code && <p className="workflow-muted">错误码：{failed.code}</p>}
        </div>
      )}
    </article>
  );
}

function RunResult({ summary, runState }) {
  return (
    <article className="panel workflow-result">
      <div className="panel-title">
        <CheckCircle2 size={18} />
        <h2>运行结果</h2>
      </div>
      <p className="workflow-result-headline">{summary.headline}</p>
      {summary.rows.length > 0 && (
        <dl className="workflow-result-rows">
          {summary.rows.map((row) => (
            <div key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {summary.audioUrl && (
        <div className="workflow-result-audio">
          <audio controls src={summary.audioUrl} />
          <a href={summary.audioUrl} download>
            下载渲染结果
          </a>
        </div>
      )}
      {summary.links.length > 0 && (
        <div className="workflow-result-links">
          {summary.links.map((link) => (
            <a key={link.href} href={link.href} onClick={(event) => navigateInApp(event, link.href)}>
              {link.label}
            </a>
          ))}
        </div>
      )}
      {runState?.result?.songCandidates?.length > 0 && (
        <ul className="workflow-candidates">
          {runState.result.songCandidates.map((candidate) => (
            <li key={`${candidate.title}-${candidate.artist}`}>
              <strong>{candidate.title}</strong> · {candidate.artist}
              <span className="workflow-muted">
                {candidate.confidence !== undefined ? `（置信度 ${candidate.confidence}）` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
      {runState?.result?.needsReview?.length > 0 && (
        <ul className="workflow-candidates">
          {runState.result.needsReview.map((item) => (
            <li key={`${item.segmentId}-${item.line}`}>
              片段 {item.segmentId} 与「{item.line}」相似度偏低（{item.confidence}），建议人工确认。
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

function RunHistory({ runs, activeRunId, onOpen, onRefresh }) {
  return (
    <article className="panel workflow-history">
      <div className="panel-title">
        <RefreshCw size={18} />
        <h2>运行记录</h2>
        <button type="button" className="workflow-inline-action" onClick={onRefresh}>刷新</button>
      </div>
      {runs.length === 0 && <p className="workflow-muted">还没有运行记录。</p>}
      <ul>
        {runs.map((run) => (
          <li key={run.runId} className={run.runId === activeRunId ? 'active' : ''}>
            <button type="button" onClick={() => onOpen(run.runId)}>
              <span className={`workflow-run-dot ${run.status}`} />
              <span className="workflow-history-main">
                <strong>{run.workflowId}</strong>
                <span className="workflow-muted">{formatDateTime(run.createdAt)}</span>
              </span>
              <span className={`workflow-run-status ${run.status}`}>{statusLabel(run.status)}</span>
            </button>
          </li>
        ))}
      </ul>
    </article>
  );
}

function statusIcon(status) {
  if (status === 'running') return <Loader2 className="spin" size={16} />;
  if (status === 'succeeded') return <CheckCircle2 size={16} />;
  if (status === 'skipped') return <MinusCircle size={16} />;
  if (status === 'failed') return <XCircle size={16} />;
  return <CircleDashed size={16} />;
}

function statusLabel(status) {
  if (status === 'queued') return '排队中';
  if (status === 'running') return '执行中';
  if (status === 'succeeded') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'interrupted') return '已中断';
  return status || '';
}

function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function navigateInApp(event, href) {
  // Keep the result links inside the SPA so the workflow page state is not reloaded from scratch.
  if (!href || href.startsWith('http')) return;
  event.preventDefault();
  window.history.pushState({}, '', href);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
