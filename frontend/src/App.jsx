import React, { useEffect, useMemo, useState } from 'react';
import { BookOpen, Download, FileAudio, FileText, Music, Play, Sparkles, Upload } from 'lucide-react';
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

  function applyChapters(nextChapters) {
    setChapters(nextChapters);
    setSelectedChapterId((currentId) => (
      nextChapters.some((chapter) => chapter.id === currentId) ? currentId : nextChapters[0]?.id || ''
    ));
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
      setProject({ ...project, hasSong: true });
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
        <div className="status-strip">
          <span>{project ? `项目 ${project.id}` : '未创建项目'}</span>
          <span>{chapters.length} 章</span>
          <span>{timeline.segments.length} 段</span>
          <span>{totals.duration}s</span>
        </div>
      </header>

      {error && <div className="notice error">{error}</div>}

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
          {songFile && <audio className="audio" src={URL.createObjectURL(songFile)} controls />}
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
                    <label>
                      歌曲片段开始
                      <input
                        type="number"
                        min="0"
                        step="0.1"
                        value={segment.songClipStartSec}
                        onChange={(event) => updateSegment(segment.id, { songClipStartSec: Number(event.target.value) })}
                      />
                    </label>
                    <label>
                      歌曲片段结束
                      <input
                        type="number"
                        min="0"
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
    </main>
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
