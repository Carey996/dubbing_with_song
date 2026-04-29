import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  BookOpen,
  Download,
  FileAudio,
  FileText,
  FolderOpen,
  History,
  Music,
  Play,
  Sparkles,
  Trash2,
  Upload,
} from 'lucide-react';
import { createRoot } from 'react-dom/client';
import { advanceAnalysisProgress, completeAnalysisProgress } from './analysisProgress.js';
import './styles.css';

const emptyTimeline = {
  bgmVolume: 0.22,
  narrationVolume: 1,
  songStartSec: 0,
  segments: [],
};

function App() {
  const [text, setText] = useState('');
  const [txtFile, setTxtFile] = useState(null);
  const [songFile, setSongFile] = useState(null);
  const [project, setProject] = useState(null);
  const [projectList, setProjectList] = useState([]);
  const [view, setView] = useState('workspace');
  const [analysis, setAnalysis] = useState(null);
  const [chapters, setChapters] = useState([]);
  const [selectedChapterId, setSelectedChapterId] = useState('');
  const [timeline, setTimeline] = useState(emptyTimeline);
  const [renderResult, setRenderResult] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [analysisProgress, setAnalysisProgress] = useState(0);
  const [analysisProgressVisible, setAnalysisProgressVisible] = useState(false);
  const [analysisTargetLabel, setAnalysisTargetLabel] = useState('');
  const [songDurationSec, setSongDurationSec] = useState(0);
  const [songCursorSec, setSongCursorSec] = useState(0);

  const totals = useMemo(() => {
    const duration = timeline.segments.reduce((sum, segment) => sum + Number(segment.durationSec || 0), 0);
    const lyricCount = timeline.segments.filter((segment) => segment.type === 'lyric').length;
    return { duration: duration.toFixed(1), lyricCount };
  }, [timeline]);

  const selectedChapter = useMemo(
    () => chapters.find((chapter) => chapter.id === selectedChapterId) || null,
    [chapters, selectedChapterId],
  );
  const previewChapter = selectedChapter || chapters[0] || null;
  const isAnalyzing = busy === 'analyzing';
  const localSongUrl = useMemo(() => (songFile ? URL.createObjectURL(songFile) : ''), [songFile]);
  const uploadedSongUrl = useMemo(() => {
    if (!project?.hasSong) return '';
    const version = encodeURIComponent(project.updatedAt || project.id);
    return `/api/projects/${project.id}/song-file?v=${version}`;
  }, [project]);
  const songAudioUrl = localSongUrl || uploadedSongUrl;
  const analysisPhase = useMemo(() => {
    if (analysisProgress >= 100) return '分析完成，正在刷新结果';
    if (analysisProgress >= 72) return '生成时间轴与歌词候选';
    if (analysisProgress >= 36) return 'AI 正在解析章节内容';
    return '准备分析上下文';
  }, [analysisProgress]);

  useEffect(() => {
    if (!isAnalyzing) return undefined;

    const timer = window.setInterval(() => {
      setAnalysisProgress((current) => advanceAnalysisProgress(current));
    }, 700);

    return () => window.clearInterval(timer);
  }, [isAnalyzing]);

  useEffect(() => {
    loadProjectList();
  }, []);

  useEffect(() => (
    () => {
      if (localSongUrl) URL.revokeObjectURL(localSongUrl);
    }
  ), [localSongUrl]);

  function applyChapters(nextChapters) {
    setChapters(nextChapters);
    setSelectedChapterId((currentId) => (
      nextChapters.some((chapter) => chapter.id === currentId) ? currentId : nextChapters[0]?.id || ''
    ));
  }

  function resetCurrentProject() {
    setProject(null);
    setText('');
    setTxtFile(null);
    setSongFile(null);
    setAnalysis(null);
    applyChapters([]);
    setTimeline(emptyTimeline);
    setRenderResult(null);
  }

  async function loadProjectList() {
    try {
      const data = await request('/api/projects');
      setProjectList(data.projects || []);
    } catch (err) {
      setError(err.message);
    }
  }

  function applyProjectDetail(data, nextChapters = null) {
    setProject(data);
    setText(data.text || '');
    setTxtFile(null);
    setSongFile(null);
    setAnalysis(data.analysis || null);
    setTimeline(data.timeline || emptyTimeline);
    setRenderResult(data.latestRender || null);
    if (nextChapters) {
      applyChapters(nextChapters);
    } else if (data.analysis?.chapters?.length) {
      applyChapters(data.analysis.chapters);
    } else {
      applyChapters([]);
    }
  }

  async function refreshProject(projectId) {
    const [detail, chapterData] = await Promise.all([
      request(`/api/projects/${projectId}`),
      request(`/api/projects/${projectId}/chapters`),
    ]);
    applyProjectDetail(detail, chapterData.chapters || []);
    await loadProjectList();
  }

  async function loadProject(projectId) {
    if (!projectId) return;
    setBusy('loading-project');
    setError('');
    try {
      await refreshProject(projectId);
      setView('workspace');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  }

  async function deleteProject(projectId) {
    if (!projectId) return;
    const item = projectList.find((candidate) => candidate.id === projectId);
    const label = item?.title || projectId;
    if (!window.confirm(`删除项目「${label}」？此操作会删除项目文件和生成结果。`)) return;

    setBusy(`deleting-${projectId}`);
    setError('');
    try {
      await request(`/api/projects/${projectId}`, { method: 'DELETE' });
      if (project?.id === projectId) {
        resetCurrentProject();
      }
      await loadProjectList();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  }

  async function createProject() {
    setBusy('creating');
    setError('');
    setRenderResult(null);
    try {
      let data;
      if (txtFile) {
        const form = new FormData();
        form.append('file', txtFile);
        data = await request('/api/projects/text-file', { method: 'POST', body: form });
        setText(await txtFile.text());
      } else {
        data = await request('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }),
        });
      }
      setProject(data);
      setAnalysis(null);
      await loadChapters(data.id);
      setTimeline(emptyTimeline);
      await loadProjectList();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  }

  async function analyzeProject() {
    if (!project) return;
    const chapterForAnalysis = selectedChapter;
    setBusy('analyzing');
    setError('');
    setAnalysisTargetLabel(chapterForAnalysis ? chapterForAnalysis.title : '全篇文本');
    setAnalysisProgress(4);
    setAnalysisProgressVisible(true);
    let completed = false;
    try {
      const url = chapterForAnalysis
        ? `/api/projects/${project.id}/chapters/${chapterForAnalysis.id}/analyze`
        : `/api/projects/${project.id}/analyze`;
      const data = await request(url, { method: 'POST' });
      setAnalysis(data);
      applyChapters(data.chapters || chapters);
      setTimeline(data.timeline);
      setAnalysisProgress(completeAnalysisProgress());
      await refreshProject(project.id);
      completed = true;
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
      if (completed) {
        window.setTimeout(() => setAnalysisProgressVisible(false), 600);
      } else {
        setAnalysisProgressVisible(false);
        setAnalysisProgress(0);
      }
    }
  }

  async function loadChapters(projectId = project?.id) {
    if (!projectId) return;
    const data = await request(`/api/projects/${projectId}/chapters`);
    applyChapters(data.chapters || []);
  }

  async function uploadSong() {
    if (!project || !songFile) return;
    setBusy('song');
    setError('');
    try {
      const form = new FormData();
      form.append('file', songFile);
      await request(`/api/projects/${project.id}/song-file`, { method: 'POST', body: form });
      await refreshProject(project.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  }

  async function saveTimeline() {
    if (!project) return;
    setBusy('timeline');
    setError('');
    try {
      const data = await request(`/api/projects/${project.id}/timeline`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(timeline),
      });
      setTimeline(data);
      await loadProjectList();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  }

  async function renderAudio() {
    if (!project) return;
    setBusy('rendering');
    setError('');
    try {
      await saveTimeline();
      const data = await request(`/api/projects/${project.id}/render`, { method: 'POST' });
      setRenderResult(data);
      await refreshProject(project.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  }

  function updateSegment(id, patch) {
    setTimeline((current) => ({
      ...current,
      segments: current.segments.map((segment) => (segment.id === id ? { ...segment, ...patch } : segment)),
    }));
  }

  function setCueFromPlayback(segment, field) {
    const cue = roundSeconds(songCursorSec);
    updateSegment(segment.id, normalizeCuePatch(segment, field, cue));
  }

  function normalizeCuePatch(segment, field, cue) {
    const patch = { [field]: cue };
    if (field === 'songClipStartSec' && Number(segment.songClipEndSec || 0) <= cue) {
      patch.songClipEndSec = roundSeconds(cue + Number(segment.durationSec || 0.5));
    }
    if (field === 'songClipEndSec' && Number(segment.songClipStartSec || 0) > cue) {
      patch.songClipStartSec = cue;
    }
    return patch;
  }

  function getCueMax(segment) {
    return Math.max(
      10,
      Number(songDurationSec || 0),
      Number(segment.songClipStartSec || 0),
      Number(segment.songClipEndSec || 0),
      Number(segment.durationSec || 0),
    );
  }

  function previewNarration() {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const narration = timeline.segments
      .filter((segment) => segment.type === 'narration')
      .map((segment) => segment.text)
      .join('。');
    const utterance = new SpeechSynthesisUtterance(narration || text);
    utterance.lang = 'zh-CN';
    window.speechSynthesis.speak(utterance);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Local workflow</p>
          <h1>AI 配音与歌曲替换</h1>
        </div>
        <div className="topbar-right">
          <div className="status-strip">
            <span>{project ? `项目 ${project.id}` : '未创建项目'}</span>
            <span>{chapters.length} 章</span>
            <span>{timeline.segments.length} 段</span>
            <span>{totals.duration}s</span>
          </div>
          <div className="view-switch">
            <button type="button" className={view === 'workspace' ? 'primary' : ''} onClick={() => setView('workspace')}>
              <Sparkles size={18} />
              工作台
            </button>
            <button type="button" className={view === 'history' ? 'primary' : ''} onClick={() => setView('history')}>
              <History size={18} />
              历史项目
            </button>
          </div>
        </div>
      </header>

      {error && <div className="notice error">{error}</div>}

      {view === 'history' ? (
        <HistoryPage
          busy={busy}
          currentProjectId={project?.id || ''}
          projects={projectList}
          onBack={() => setView('workspace')}
          onDelete={deleteProject}
          onOpen={loadProject}
        />
      ) : (
      <section className="workspace-grid">
        <aside className="side-panel">
          <PanelTitle icon={<FileText />} title="输入" />
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="粘贴文案，或上传 txt 文件。"
          />
          <label className="file-picker">
            <Upload size={18} />
            <span>{txtFile ? txtFile.name : '选择 txt 文件'}</span>
            <input
              type="file"
              accept=".txt,text/plain"
              onChange={(event) => setTxtFile(event.target.files?.[0] || null)}
            />
          </label>
          <button onClick={createProject} disabled={busy || (!text.trim() && !txtFile)} className="primary">
            <Upload size={18} />
            创建项目
          </button>
          {projectList.length > 0 && (
            <div className="project-history">
              <PanelTitle icon={<BookOpen />} title="历史项目" />
              {projectList.map((item) => (
                <div key={item.id} className={`project-history-row ${project?.id === item.id ? 'active' : ''}`}>
                  <button
                    type="button"
                    className="project-history-item"
                    onClick={() => loadProject(item.id)}
                    disabled={busy}
                  >
                    <span>{item.title || item.id}</span>
                    <small>{item.status} · {item.textLength} 字</small>
                  </button>
                  <button
                    type="button"
                    className="icon-button danger"
                    onClick={() => deleteProject(item.id)}
                    disabled={busy}
                    aria-label={`删除 ${item.title || item.id}`}
                    title="删除项目"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <button onClick={analyzeProject} disabled={busy || !project}>
            {isAnalyzing ? <span className="spinner" aria-hidden="true" /> : <Sparkles size={18} />}
            {isAnalyzing ? '分析中' : selectedChapter ? '分析当前章节' : 'AI 分析'}
          </button>
          {analysisProgressVisible && (
            <div className="analysis-progress" role="status" aria-live="polite">
              <div className="analysis-progress-head">
                <strong>{analysisTargetLabel}</strong>
                <span>{Math.round(analysisProgress)}%</span>
              </div>
              <div className="progress-track" aria-hidden="true">
                <div className="progress-fill" style={{ width: `${analysisProgress}%` }} />
              </div>
              <small>{analysisPhase}</small>
            </div>
          )}
          <button onClick={() => loadChapters()} disabled={busy || !project}>
            <FileText size={18} />
            章节预览
          </button>

          <PanelTitle icon={<Music />} title="歌曲文件" />
          <label className="file-picker">
            <FileAudio size={18} />
            <span>{songFile ? songFile.name : '选择 MP3'}</span>
            <input
              type="file"
              accept=".mp3,audio/mpeg"
              onChange={(event) => setSongFile(event.target.files?.[0] || null)}
            />
          </label>
          <button onClick={uploadSong} disabled={busy || !project || !songFile}>
            <Upload size={18} />
            上传 MP3
          </button>
          {songAudioUrl && (
            <audio
              className="audio"
              src={songAudioUrl}
              controls
              preload="metadata"
              onLoadedMetadata={(event) => setSongDurationSec(roundSeconds(event.currentTarget.duration || 0))}
              onTimeUpdate={(event) => setSongCursorSec(roundSeconds(event.currentTarget.currentTime || 0))}
            />
          )}
        </aside>

        <section className="main-panel">
          <div className="toolbar">
            <div>
              <h2>时间轴确认</h2>
              <p>{totals.lyricCount} 个歌词候选，用户确认后再导出。</p>
            </div>
            <div className="actions">
              <button onClick={previewNarration} disabled={!timeline.segments.length && !text}>
                <Play size={18} />
                浏览器试听旁白
              </button>
              <button onClick={renderAudio} disabled={busy || !project || !timeline.segments.length} className="primary">
                <Download size={18} />
                生成音频
              </button>
            </div>
          </div>

          <div className="mix-controls">
            <label>
              BGM 音量
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={timeline.bgmVolume}
                onChange={(event) => setTimeline({ ...timeline, bgmVolume: Number(event.target.value) })}
              />
              <strong>{Math.round(timeline.bgmVolume * 100)}%</strong>
            </label>
            <label>
              歌曲起点秒
              <input
                type="number"
                min="0"
                step="0.1"
                value={timeline.songStartSec}
                onChange={(event) => setTimeline({ ...timeline, songStartSec: Number(event.target.value) })}
              />
            </label>
            <button onClick={saveTimeline} disabled={busy || !project || !timeline.segments.length}>
              保存时间轴
            </button>
          </div>

          {analysis?.songCandidates?.length > 0 && (
            <div className="song-candidates">
              {analysis.songCandidates.map((candidate) => (
                <article key={`${candidate.title}-${candidate.artist}`}>
                  <div>
                    <strong>{candidate.title}</strong>
                    <span>{candidate.artist}</span>
                  </div>
                  <p>{candidate.matchedLyrics.join(' / ')}</p>
                  <small>置信度 {Math.round(candidate.confidence * 100)}% · {candidate.note}</small>
                </article>
              ))}
            </div>
          )}

          {chapters.length > 0 && (
            <div className="chapter-preview">
              <div className="chapter-actions">
                <button type="button" onClick={() => setSelectedChapterId('')} className={!selectedChapterId ? 'primary' : ''}>
                  全篇
                </button>
              </div>
              <div className="chapter-list" aria-label="章节预览">
                {chapters.map((chapter) => (
                  <button
                    key={chapter.id}
                    type="button"
                    className={`chapter-button ${selectedChapter?.id === chapter.id ? 'active' : ''}`}
                    onClick={() => setSelectedChapterId(chapter.id)}
                  >
                    <BookOpen size={16} />
                    <span>{chapter.title}</span>
                    <small>{chapter.textLength} 字</small>
                  </button>
                ))}
              </div>
              {previewChapter && (
                <article className="chapter-detail">
                  <div>
                    <strong>{previewChapter.title}</strong>
                    <span>{previewChapter.textLength} 字</span>
                  </div>
                  <p>{previewChapter.text}</p>
                </article>
              )}
            </div>
          )}

          <div className="segment-list">
            {timeline.segments.map((segment) => (
              <article className={`segment ${segment.type}`} key={segment.id}>
                <div className="segment-head">
                  <select value={segment.type} onChange={(event) => updateSegment(segment.id, { type: event.target.value })}>
                    <option value="narration">旁白</option>
                    <option value="lyric">歌词</option>
                  </select>
                  <span className="chapter-pill">{segment.chapterTitle || '正文'}</span>
                  <span>{segment.startSec}s</span>
                  <label>
                    时长
                    <input
                      type="number"
                      min="0.5"
                      step="0.1"
                      value={segment.durationSec}
                      onChange={(event) => updateSegment(segment.id, { durationSec: Number(event.target.value) })}
                    />
                  </label>
                </div>
                <textarea value={segment.text} onChange={(event) => updateSegment(segment.id, { text: event.target.value })} />
                {segment.type === 'lyric' && (
                  <div className="lyric-controls">
                    <div className="lyric-song-panel">
                      <label className="file-picker compact">
                        <FileAudio size={18} />
                        <span>{songFile ? songFile.name : project?.hasSong ? '替换 MP3' : '选择 MP3'}</span>
                        <input
                          type="file"
                          accept=".mp3,audio/mpeg"
                          onChange={(event) => setSongFile(event.target.files?.[0] || null)}
                        />
                      </label>
                      <button onClick={uploadSong} disabled={busy || !project || !songFile}>
                        <Upload size={18} />
                        上传
                      </button>
                      {songAudioUrl && (
                        <audio
                          className="audio"
                          src={songAudioUrl}
                          controls
                          preload="metadata"
                          onLoadedMetadata={(event) => setSongDurationSec(roundSeconds(event.currentTarget.duration || 0))}
                          onTimeUpdate={(event) => setSongCursorSec(roundSeconds(event.currentTarget.currentTime || 0))}
                        />
                      )}
                    </div>
                    <label>
                      歌曲片段开始
                      <div className="cue-input-row">
                        <input
                          type="number"
                          min="0"
                          step="0.1"
                          value={segment.songClipStartSec}
                          onChange={(event) => updateSegment(segment.id, { songClipStartSec: Number(event.target.value) })}
                        />
                        <button type="button" onClick={() => setCueFromPlayback(segment, 'songClipStartSec')} disabled={!songAudioUrl}>
                          <Music size={16} />
                          当前
                        </button>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max={getCueMax(segment)}
                        step="0.1"
                        value={segment.songClipStartSec}
                        onChange={(event) => updateSegment(segment.id, { songClipStartSec: Number(event.target.value) })}
                      />
                    </label>
                    <label>
                      歌曲片段结束
                      <div className="cue-input-row">
                        <input
                          type="number"
                          min="0"
                          step="0.1"
                          value={segment.songClipEndSec}
                          onChange={(event) => updateSegment(segment.id, { songClipEndSec: Number(event.target.value) })}
                        />
                        <button type="button" onClick={() => setCueFromPlayback(segment, 'songClipEndSec')} disabled={!songAudioUrl}>
                          <Music size={16} />
                          当前
                        </button>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max={getCueMax(segment)}
                        step="0.1"
                        value={segment.songClipEndSec}
                        onChange={(event) => updateSegment(segment.id, { songClipEndSec: Number(event.target.value) })}
                      />
                    </label>
                  </div>
                )}
                <small>{segment.reason} · {Math.round((segment.confidence || 0) * 100)}%</small>
              </article>
            ))}
          </div>

          {renderResult && (
            <div className="notice success">
              <strong>{renderResult.message}</strong>
              {renderResult.warnings?.map((warning) => <span key={warning}>{warning}</span>)}
              <a href={renderResult.outputUrl} target="_blank" rel="noreferrer">下载结果</a>
            </div>
          )}
        </section>
      </section>
      )}
    </main>
  );
}

function HistoryPage({ busy, currentProjectId, projects, onBack, onDelete, onOpen }) {
  return (
    <section className="history-page">
      <div className="history-toolbar">
        <button type="button" onClick={onBack}>
          <ArrowLeft size={18} />
          返回工作台
        </button>
        <div>
          <h2>历史项目</h2>
          <p>{projects.length} 个项目</p>
        </div>
      </div>
      {projects.length === 0 ? (
        <div className="empty-state">
          <History size={28} />
          <strong>暂无历史项目</strong>
        </div>
      ) : (
        <div className="history-list">
          {projects.map((item) => (
            <article key={item.id} className={`history-card ${currentProjectId === item.id ? 'active' : ''}`}>
              <div className="history-card-main">
                <strong>{item.title || item.id}</strong>
                <span>{item.id}</span>
              </div>
              <div className="history-meta">
                <span>{item.status}</span>
                <span>{item.textLength} 字</span>
                <span>{formatDateTime(item.updatedAt)}</span>
              </div>
              <div className="history-flags">
                <span className={item.hasAnalysis ? 'ready' : ''}>分析</span>
                <span className={item.hasSong ? 'ready' : ''}>歌曲</span>
                <span className={item.hasTimeline ? 'ready' : ''}>时间轴</span>
                <span className={item.hasRender ? 'ready' : ''}>生成</span>
              </div>
              <div className="history-actions">
                <button type="button" onClick={() => onOpen(item.id)} disabled={busy}>
                  <FolderOpen size={18} />
                  打开
                </button>
                <button type="button" className="danger" onClick={() => onDelete(item.id)} disabled={busy}>
                  <Trash2 size={18} />
                  删除
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function PanelTitle({ icon, title }) {
  return (
    <div className="panel-title">
      {icon}
      <h2>{title}</h2>
    </div>
  );
}

function roundSeconds(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.round(numeric * 10) / 10);
}

function formatDateTime(value) {
  if (!value) return '未知时间';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(typeof data === 'string' ? data : data.detail || '请求失败');
  }
  return data;
}

createRoot(document.getElementById('root')).render(<App />);
