import React, { useEffect, useMemo, useRef, useState } from 'react';
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
import {
  filterSegmentsByChapter,
  getClosestSegmentIndexByViewportCenter,
  getNearestLyricSegmentIndex,
  getSegmentKindLabel,
  getSelectedSegment,
  normalizeSegmentIndex,
} from './segmentNavigator.js';
import {
  buildWorkflowPath,
  getWorkflowPages,
  getWorkflowRouteDataNeeds,
  parseWorkflowRoute,
  resolveSelectedChapterIdForRoute,
} from './workflowNavigation.js';
import './styles.css';

const emptyTimeline = {
  bgmVolume: 0.22,
  narrationVolume: 1,
  songStartSec: 0,
  segments: [],
};

const pageIcons = {
  input: FileText,
  chapters: BookOpen,
  analysis: Sparkles,
};

function App() {
  const [text, setText] = useState('');
  const [txtFile, setTxtFile] = useState(null);
  const [songFile, setSongFile] = useState(null);
  const [project, setProject] = useState(null);
  const [projectList, setProjectList] = useState([]);
  const [route, setRoute] = useState(() => parseWorkflowRoute(window.location.pathname));
  const [analysis, setAnalysis] = useState(null);
  const [chapters, setChapters] = useState([]);
  const [selectedChapterId, setSelectedChapterId] = useState('');
  const [selectedSegmentIndex, setSelectedSegmentIndex] = useState(0);
  const [timeline, setTimeline] = useState(emptyTimeline);
  const [renderResult, setRenderResult] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [analysisProgress, setAnalysisProgress] = useState(0);
  const [analysisProgressVisible, setAnalysisProgressVisible] = useState(false);
  const [analysisTargetLabel, setAnalysisTargetLabel] = useState('');
  const [songDurationSec, setSongDurationSec] = useState(0);
  const [songCursorSec, setSongCursorSec] = useState(0);
  const segmentRefs = useRef({});
  const shouldScrollSelectedSegment = useRef(false);
  const routeRequestId = useRef(0);

  const page = route.page;
  const routeKey = `${route.page}:${route.projectId}:${route.chapterId}`;
  const segments = timeline.segments || [];
  const selectedChapter = useMemo(
    () => chapters.find((chapter) => chapter.id === selectedChapterId) || null,
    [chapters, selectedChapterId],
  );
  const visibleSegments = useMemo(
    () => filterSegmentsByChapter(segments, selectedChapter),
    [segments, selectedChapter],
  );
  const totals = useMemo(() => {
    const duration = segments.reduce((sum, segment) => sum + Number(segment.durationSec || 0), 0);
    const lyricCount = segments.filter((segment) => segment.type === 'lyric').length;
    return { duration: duration.toFixed(1), lyricCount };
  }, [segments]);
  const visibleTotals = useMemo(() => {
    const duration = visibleSegments.reduce((sum, segment) => sum + Number(segment.durationSec || 0), 0);
    const lyricCount = visibleSegments.filter((segment) => segment.type === 'lyric').length;
    return { duration: duration.toFixed(1), lyricCount };
  }, [visibleSegments]);

  const workflowPages = useMemo(() => getWorkflowPages({ project, timeline }), [project, timeline]);
  const previewChapter = selectedChapter || chapters[0] || null;
  const { segment: selectedSegment, index: normalizedSegmentIndex } = useMemo(
    () => getSelectedSegment(visibleSegments, selectedSegmentIndex),
    [visibleSegments, selectedSegmentIndex],
  );
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
    const handlePopState = () => setRoute(parseWorkflowRoute(window.location.pathname));
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    const dataNeeds = getWorkflowRouteDataNeeds(route);
    const requestId = routeRequestId.current + 1;
    routeRequestId.current = requestId;
    let cancelled = false;

    async function loadRouteData() {
      setError('');

      if (!dataNeeds.projectDetail) {
        if (route.page === 'input' && !route.projectId) {
          clearCurrentProjectState();
        }
        if (dataNeeds.projectList) {
          await loadProjectList();
        }
        return;
      }

      setBusy('loading-project');
      try {
        const detail = await request(`/api/projects/${route.projectId}`);
        const nextChapters = dataNeeds.chapters
          ? (await request(`/api/projects/${route.projectId}/chapters`)).chapters || []
          : null;
        if (cancelled || routeRequestId.current !== requestId) return;
        applyProjectDetail(detail, nextChapters);
        if (nextChapters) {
          setSelectedChapterId(resolveSelectedChapterIdForRoute({
            route,
            chapters: nextChapters,
            currentChapterId: selectedChapterId,
          }));
        }
        if (dataNeeds.projectList) {
          await loadProjectList();
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled && routeRequestId.current === requestId) {
          setBusy('');
        }
      }
    }

    loadRouteData();

    return () => {
      cancelled = true;
    };
  }, [routeKey]);

  useEffect(() => {
    setSelectedSegmentIndex((currentIndex) => normalizeSegmentIndex(currentIndex, visibleSegments));
  }, [visibleSegments]);

  useEffect(() => {
    if (page !== 'analysis' || !selectedSegment) return;
    if (!shouldScrollSelectedSegment.current) return;
    shouldScrollSelectedSegment.current = false;
    segmentRefs.current[selectedSegment.id]?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [page, normalizedSegmentIndex, selectedSegment]);

  useEffect(() => {
    if (page !== 'analysis' || visibleSegments.length === 0) return undefined;

    let frameId = 0;
    const syncSelectedSegmentToScroll = () => {
      if (frameId) return;
      frameId = window.requestAnimationFrame(() => {
        frameId = 0;
        const measurements = visibleSegments
          .map((segment, index) => {
            const node = segmentRefs.current[segment.id];
            if (!node) return null;
            const rect = node.getBoundingClientRect();
            return { index, top: rect.top, bottom: rect.bottom };
          })
          .filter(Boolean);
        const nextIndex = getClosestSegmentIndexByViewportCenter(measurements, window.innerHeight);
        setSelectedSegmentIndex((currentIndex) => (currentIndex === nextIndex ? currentIndex : nextIndex));
      });
    };

    window.addEventListener('scroll', syncSelectedSegmentToScroll, { passive: true });
    window.addEventListener('resize', syncSelectedSegmentToScroll);
    syncSelectedSegmentToScroll();

    return () => {
      window.removeEventListener('scroll', syncSelectedSegmentToScroll);
      window.removeEventListener('resize', syncSelectedSegmentToScroll);
      if (frameId) window.cancelAnimationFrame(frameId);
    };
  }, [page, visibleSegments]);

  useEffect(() => (
    () => {
      if (localSongUrl) URL.revokeObjectURL(localSongUrl);
    }
  ), [localSongUrl]);

  function navigateTo(nextRoute, { replace = false } = {}) {
    const normalizedRoute = {
      page: nextRoute.page || 'input',
      projectId: nextRoute.projectId || '',
      chapterId: nextRoute.chapterId || '',
    };
    const nextPath = buildWorkflowPath(normalizedRoute);
    if (window.location.pathname !== nextPath) {
      window.history[replace ? 'replaceState' : 'pushState']({}, '', nextPath);
    }
    setRoute(parseWorkflowRoute(nextPath));
  }

  function applyChapters(nextChapters) {
    setChapters(nextChapters);
    setSelectedChapterId((currentId) => (
      nextChapters.some((chapter) => chapter.id === currentId) ? currentId : nextChapters[0]?.id || ''
    ));
  }

  function clearCurrentProjectState() {
    setProject(null);
    setText('');
    setTxtFile(null);
    setSongFile(null);
    setAnalysis(null);
    applyChapters([]);
    setTimeline(emptyTimeline);
    setRenderResult(null);
    setSelectedSegmentIndex(0);
  }

  function resetCurrentProject() {
    clearCurrentProjectState();
    navigateTo({ page: 'input' }, { replace: true });
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
    setSelectedSegmentIndex(0);
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
    const nextChapters = chapterData.chapters || [];
    applyProjectDetail(detail, nextChapters);
    await loadProjectList();
    return { detail, chapters: nextChapters };
  }

  function loadProject(projectId, nextPage = 'chapters') {
    if (!projectId) return;
    navigateTo({ page: nextPage === 'auto' ? 'chapters' : nextPage, projectId });
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
      if (project?.id === projectId || route.projectId === projectId) {
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
      setTimeline(emptyTimeline);
      await loadChapters(data.id);
      await loadProjectList();
      navigateTo({ page: 'chapters', projectId: data.id });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  }

  async function analyzeProject(chapterForAnalysis = selectedChapter) {
    if (!project) return;
    setSelectedChapterId(chapterForAnalysis?.id || '');
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
      setSelectedSegmentIndex(0);
      setAnalysisProgress(completeAnalysisProgress());
      await refreshProject(project.id);
      navigateTo({
        page: 'analysis',
        projectId: project.id,
        chapterId: chapterForAnalysis?.id || '',
      });
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
    const narration = segments
      .filter((segment) => segment.type === 'narration')
      .map((segment) => segment.text)
      .join('。');
    const utterance = new SpeechSynthesisUtterance(narration || text);
    utterance.lang = 'zh-CN';
    window.speechSynthesis.speak(utterance);
  }

  function selectSegment(index) {
    shouldScrollSelectedSegment.current = true;
    setSelectedSegmentIndex(normalizeSegmentIndex(index, visibleSegments));
    if (project) {
      navigateTo({
        page: 'analysis',
        projectId: project.id,
        chapterId: selectedChapter?.id || '',
      }, { replace: true });
    }
  }

  function jumpToNearestLyric() {
    shouldScrollSelectedSegment.current = true;
    setSelectedSegmentIndex((currentIndex) => getNearestLyricSegmentIndex(visibleSegments, currentIndex));
    if (project) {
      navigateTo({
        page: 'analysis',
        projectId: project.id,
        chapterId: selectedChapter?.id || '',
      }, { replace: true });
    }
  }

  const commonAudioProps = {
    songAudioUrl,
    setSongDurationSec,
    setSongCursorSec,
  };

  return (
    <main className={`app-shell ${page === 'analysis' && segments.length ? 'has-segment-navigator' : ''}`}>
      <header className="topbar">
        <div>
          <p className="eyebrow">Local workflow</p>
          <h1>AI 配音与歌曲替换</h1>
        </div>
        <div className="topbar-right">
          <div className="status-strip">
            <span>{project ? `项目 ${project.id}` : '未创建项目'}</span>
            <span>{chapters.length} 章</span>
            <span>{segments.length} 段</span>
            <span>{totals.duration}s</span>
          </div>
          <div className="topbar-actions">
            <div className="workflow-stepper" aria-label="工作流页面">
              {workflowPages.map((item, index) => {
              const Icon = pageIcons[item.id] || Sparkles;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`workflow-step ${page === item.id ? 'primary' : ''}`}
                  onClick={() => navigateTo({
                    page: item.id,
                    projectId: project?.id || route.projectId,
                    chapterId: item.id === 'input' ? '' : selectedChapter?.id || route.chapterId,
                  })}
                >
                  <span className="step-number">{index + 1}</span>
                  <Icon size={18} />
                  {item.label}
                </button>
              );
            })}
            </div>
            <button
              type="button"
              className={`history-entry ${page === 'history' ? 'primary' : ''}`}
              onClick={() => navigateTo({ page: 'history' })}
            >
              <History size={18} />
              历史项目
            </button>
          </div>
        </div>
      </header>

      {error && <div className="notice error">{error}</div>}

      {page === 'history' && (
        <HistoryPage
          busy={busy}
          currentProjectId={project?.id || ''}
          projects={projectList}
          onBack={() => navigateTo({ page: 'input', projectId: project?.id || route.projectId })}
          onDelete={deleteProject}
          onOpen={(projectId) => loadProject(projectId)}
        />
      )}

      {page === 'input' && (
        <InputPage
          busy={busy}
          project={project}
          projectList={projectList}
          text={text}
          txtFile={txtFile}
          onCreateProject={createProject}
          onDeleteProject={deleteProject}
          onLoadProject={loadProject}
          onSetText={setText}
          onSetTxtFile={setTxtFile}
        />
      )}

      {page === 'chapters' && (
        <ChaptersPage
          analysisPhase={analysisPhase}
          analysisProgress={analysisProgress}
          analysisProgressVisible={analysisProgressVisible}
          analysisTargetLabel={analysisTargetLabel}
          busy={busy}
          chapters={chapters}
          isAnalyzing={isAnalyzing}
          previewChapter={previewChapter}
          selectedChapter={selectedChapter}
          selectedChapterId={selectedChapterId}
          onAnalyzeChapter={() => analyzeProject(selectedChapter)}
          onAnalyzeFull={() => analyzeProject(null)}
          onLoadChapters={() => loadChapters()}
          onSelectChapter={(chapterId) => {
            setSelectedChapterId(chapterId);
            if (project) {
              navigateTo({ page: 'chapters', projectId: project.id, chapterId }, { replace: true });
            }
          }}
        />
      )}

      {page === 'analysis' && (
        <AnalysisPage
          analysis={analysis}
          busy={busy}
          project={project}
          renderResult={renderResult}
          selectedSegmentId={selectedSegment?.id || ''}
          selectedChapter={selectedChapter}
          segments={visibleSegments}
          segmentRefs={segmentRefs}
          songFile={songFile}
          songAudioUrl={songAudioUrl}
          text={text}
          timeline={timeline}
          totals={visibleTotals}
          onBackToChapters={() => navigateTo({
            page: 'chapters',
            projectId: project?.id || route.projectId,
            chapterId: selectedChapter?.id || route.chapterId,
          })}
          onAnalyzeChapter={() => analyzeProject(selectedChapter)}
          onPreviewNarration={previewNarration}
          onRenderAudio={renderAudio}
          onSaveTimeline={saveTimeline}
          onSelectSegment={selectSegment}
          onSetCueFromPlayback={setCueFromPlayback}
          onSetSongFile={setSongFile}
          onSetTimeline={setTimeline}
          onUpdateSegment={updateSegment}
          onUploadSong={uploadSong}
          getCueMax={getCueMax}
          {...commonAudioProps}
        />
      )}

      {page === 'analysis' && visibleSegments.length > 0 && (
        <SegmentNavigator
          index={normalizedSegmentIndex}
          segment={selectedSegment}
          segments={visibleSegments}
          onJumpToLyric={jumpToNearestLyric}
          onSelect={selectSegment}
        />
      )}
    </main>
  );
}

function InputPage({
  busy,
  project,
  projectList,
  text,
  txtFile,
  onCreateProject,
  onDeleteProject,
  onLoadProject,
  onSetText,
  onSetTxtFile,
}) {
  return (
    <section className="page-layout input-page">
      <section className="page-main">
        <PanelTitle icon={<FileText />} title="项目输入" />
        <textarea
          className="source-editor"
          value={text}
          onChange={(event) => onSetText(event.target.value)}
          placeholder="粘贴文案，或上传 txt 文件。"
        />
        <div className="input-actions">
          <label className="file-picker">
            <Upload size={18} />
            <span>{txtFile ? txtFile.name : '选择 txt 文件'}</span>
            <input
              type="file"
              accept=".txt,text/plain"
              onChange={(event) => onSetTxtFile(event.target.files?.[0] || null)}
            />
          </label>
          <button onClick={onCreateProject} disabled={busy || (!text.trim() && !txtFile)} className="primary">
            <Upload size={18} />
            创建项目
          </button>
        </div>
      </section>

      <aside className="page-side">
        <PanelTitle icon={<BookOpen />} title="当前项目" />
        <div className="project-summary">
          <strong>{project?.title || project?.id || '尚未选择项目'}</strong>
          <span>{project ? `${project.textLength || text.length} 字` : '创建或打开项目后进入章节预览'}</span>
        </div>
        {projectList.length > 0 && (
          <div className="project-history">
            <PanelTitle icon={<History />} title="最近项目" />
            {projectList.slice(0, 5).map((item) => (
              <div key={item.id} className={`project-history-row ${project?.id === item.id ? 'active' : ''}`}>
                <button
                  type="button"
                  className="project-history-item"
                  onClick={() => onLoadProject(item.id)}
                  disabled={busy}
                >
                  <span>{item.title || item.id}</span>
                  <small>{item.status} · {item.textLength} 字</small>
                </button>
                <button
                  type="button"
                  className="icon-button danger"
                  onClick={() => onDeleteProject(item.id)}
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
      </aside>
    </section>
  );
}

function ChaptersPage({
  analysisPhase,
  analysisProgress,
  analysisProgressVisible,
  analysisTargetLabel,
  busy,
  chapters,
  isAnalyzing,
  previewChapter,
  selectedChapter,
  selectedChapterId,
  onAnalyzeChapter,
  onAnalyzeFull,
  onLoadChapters,
  onSelectChapter,
}) {
  return (
    <section className="page-layout chapters-page">
      <section className="page-main">
        <div className="page-toolbar">
          <div>
            <h2>章节预览</h2>
            <p>先确认章节和正文，再选择全篇或单章进入分析。</p>
          </div>
          <div className="actions">
            <button type="button" onClick={onLoadChapters} disabled={busy}>
              <FileText size={18} />
              刷新章节
            </button>
            <button type="button" onClick={onAnalyzeFull} disabled={busy} className={!selectedChapterId ? 'primary' : ''}>
              {isAnalyzing ? <span className="spinner" aria-hidden="true" /> : <Sparkles size={18} />}
              分析全篇
            </button>
            <button type="button" onClick={onAnalyzeChapter} disabled={busy || !selectedChapter} className={selectedChapter ? 'primary' : ''}>
              {isAnalyzing ? <span className="spinner" aria-hidden="true" /> : <Sparkles size={18} />}
              分析当前章节
            </button>
          </div>
        </div>
        {analysisProgressVisible && (
          <AnalysisProgress
            label={analysisTargetLabel}
            phase={analysisPhase}
            progress={analysisProgress}
          />
        )}
        {chapters.length > 0 ? (
          <div className="chapter-workspace">
            <div className="chapter-actions">
              <button type="button" onClick={() => onSelectChapter('')} className={!selectedChapterId ? 'primary' : ''}>
                全篇
              </button>
            </div>
            <div className="chapter-list" aria-label="章节预览">
              {chapters.map((chapter) => (
                <button
                  key={chapter.id}
                  type="button"
                  className={`chapter-button ${selectedChapter?.id === chapter.id ? 'active' : ''}`}
                  onClick={() => onSelectChapter(chapter.id)}
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
        ) : (
          <EmptyState icon={<BookOpen size={28} />} title="暂无章节" />
        )}
      </section>
    </section>
  );
}

function AnalysisPage({
  analysis,
  busy,
  project,
  renderResult,
  selectedChapter,
  selectedSegmentId,
  segments,
  segmentRefs,
  songFile,
  songAudioUrl,
  text,
  timeline,
  totals,
  onAnalyzeChapter,
  onBackToChapters,
  onPreviewNarration,
  onRenderAudio,
  onSaveTimeline,
  onSelectSegment,
  onSetCueFromPlayback,
  onSetSongFile,
  onSetTimeline,
  onUpdateSegment,
  onUploadSong,
  getCueMax,
  setSongDurationSec,
  setSongCursorSec,
}) {
  const scopeLabel = selectedChapter ? selectedChapter.title : '全篇';
  const scopeMeta = selectedChapter
    ? `${selectedChapter.textLength} 字 · 当前显示本章节片段`
    : '当前显示全部分析片段';

  return (
    <section className="page-layout analysis-page">
      <section className="page-main">
        <div className="page-toolbar">
          <div>
            <h2>分析与导出</h2>
            <p>{totals.lyricCount} 个歌词候选。先校对片段，再在同一页确认混音并生成音频。</p>
          </div>
          <div className="actions">
            <button onClick={onPreviewNarration} disabled={!segments.length && !text}>
              <Play size={18} />
              试听旁白
            </button>
            <button type="button" onClick={onSaveTimeline} disabled={busy || !project || !segments.length}>
              保存时间轴
            </button>
            <button onClick={onRenderAudio} disabled={busy || !project || !segments.length} className="primary">
              <Download size={18} />
              生成音频
            </button>
          </div>
        </div>

        <div className="analysis-scope">
          <div>
            <span>当前范围</span>
            <strong>{scopeLabel}</strong>
            <small>{scopeMeta}</small>
          </div>
          <button type="button" onClick={onBackToChapters}>
            <BookOpen size={18} />
            回章节选择
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

        {segments.length > 0 ? (
          <div className="segment-list">
            {segments.map((segment, index) => (
              <SegmentCard
                key={segment.id}
                audioProps={{ songAudioUrl, setSongDurationSec, setSongCursorSec }}
                busy={busy}
                getCueMax={getCueMax}
                isSelected={selectedSegmentId === segment.id}
                project={project}
                refCallback={(node) => {
                  if (node) segmentRefs.current[segment.id] = node;
                }}
                segment={segment}
                songFile={songFile}
                onSelect={() => onSelectSegment(index)}
                onSetCueFromPlayback={onSetCueFromPlayback}
                onSetSongFile={onSetSongFile}
                onUpdateSegment={onUpdateSegment}
                onUploadSong={onUploadSong}
              />
            ))}
          </div>
        ) : (
          <div className="empty-state action-empty">
            <Sparkles size={28} />
            <strong>{selectedChapter ? '当前章节还没有分析片段' : '暂无分析片段'}</strong>
            {selectedChapter && <span>先回章节页确认范围，或直接重新分析当前章节。</span>}
            {selectedChapter && (
              <button type="button" className="primary" onClick={onAnalyzeChapter} disabled={busy || !project}>
                <Sparkles size={18} />
                分析当前章节
              </button>
            )}
          </div>
        )}

        <section className="export-panel">
          <div>
            <h2>导出设置</h2>
            <p>这些参数会应用到当前分析时间轴。</p>
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
                onChange={(event) => onSetTimeline({ ...timeline, bgmVolume: Number(event.target.value) })}
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
                onChange={(event) => onSetTimeline({ ...timeline, songStartSec: Number(event.target.value) })}
              />
            </label>
            <button onClick={onSaveTimeline} disabled={busy || !project || !segments.length}>
              保存时间轴
            </button>
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
    </section>
  );
}

function SegmentCard({
  audioProps,
  busy,
  getCueMax,
  isSelected,
  project,
  refCallback,
  segment,
  songFile,
  onSelect,
  onSetCueFromPlayback,
  onSetSongFile,
  onUpdateSegment,
  onUploadSong,
}) {
  return (
    <article
      ref={refCallback}
      className={`segment ${segment.type} ${isSelected ? 'selected' : ''}`}
      onClick={onSelect}
    >
      <div className="segment-head">
        <select value={segment.type} onChange={(event) => onUpdateSegment(segment.id, { type: event.target.value })}>
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
            onChange={(event) => onUpdateSegment(segment.id, { durationSec: Number(event.target.value) })}
          />
        </label>
      </div>
      <textarea value={segment.text} onChange={(event) => onUpdateSegment(segment.id, { text: event.target.value })} />
      {segment.type === 'lyric' && (
        <div className="lyric-controls">
          <SongUploadPanel
            busy={busy}
            project={project}
            songFile={songFile}
            onSetSongFile={onSetSongFile}
            onUploadSong={onUploadSong}
            {...audioProps}
          />
          <CueControl
            label="歌曲片段开始"
            max={getCueMax(segment)}
            value={segment.songClipStartSec}
            onCapture={() => onSetCueFromPlayback(segment, 'songClipStartSec')}
            onChange={(value) => onUpdateSegment(segment.id, { songClipStartSec: value })}
            disabled={!audioProps.songAudioUrl}
          />
          <CueControl
            label="歌曲片段结束"
            max={getCueMax(segment)}
            value={segment.songClipEndSec}
            onCapture={() => onSetCueFromPlayback(segment, 'songClipEndSec')}
            onChange={(value) => onUpdateSegment(segment.id, { songClipEndSec: value })}
            disabled={!audioProps.songAudioUrl}
          />
        </div>
      )}
      <small>{segment.reason} · {Math.round((segment.confidence || 0) * 100)}%</small>
    </article>
  );
}

function SongUploadPanel({
  busy,
  project,
  songFile,
  songAudioUrl,
  setSongDurationSec,
  setSongCursorSec,
  onSetSongFile,
  onUploadSong,
}) {
  return (
    <div className="lyric-song-panel">
      <label className="file-picker compact">
        <FileAudio size={18} />
        <span>{songFile ? songFile.name : project?.hasSong ? '替换 MP3' : '选择 MP3'}</span>
        <input
          type="file"
          accept=".mp3,audio/mpeg"
          onChange={(event) => onSetSongFile(event.target.files?.[0] || null)}
        />
      </label>
      <button onClick={onUploadSong} disabled={busy || !project || !songFile}>
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
  );
}

function CueControl({ disabled, label, max, value, onCapture, onChange }) {
  return (
    <label>
      {label}
      <div className="cue-input-row">
        <input
          type="number"
          min="0"
          step="0.1"
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        <button type="button" onClick={onCapture} disabled={disabled}>
          <Music size={16} />
          当前
        </button>
      </div>
      <input
        type="range"
        min="0"
        max={max}
        step="0.1"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function SegmentNavigator({ index, segment, segments, onJumpToLyric, onSelect }) {
  const label = getSegmentKindLabel(segment);
  const preview = segment?.text?.replace(/\s+/g, ' ').slice(0, 80) || '暂无内容';

  return (
    <aside className={`segment-navigator ${segment?.type === 'lyric' ? 'lyric' : 'narration'}`} aria-label="分析片段定位">
      <div className="segment-navigator-meta">
        <span>{index + 1} / {segments.length}</span>
        <strong>{label}</strong>
        <span>{segment?.chapterTitle || '正文'}</span>
      </div>
      <input
        type="range"
        min="0"
        max={segments.length - 1}
        step="1"
        value={index}
        onChange={(event) => onSelect(Number(event.target.value))}
      />
      <div className="segment-navigator-preview">
        <span>{preview}</span>
        <button type="button" onClick={onJumpToLyric}>
          <Music size={16} />
          定位到歌词
        </button>
      </div>
    </aside>
  );
}

function AnalysisProgress({ label, phase, progress }) {
  return (
    <div className="analysis-progress" role="status" aria-live="polite">
      <div className="analysis-progress-head">
        <strong>{label}</strong>
        <span>{Math.round(progress)}%</span>
      </div>
      <div className="progress-track" aria-hidden="true">
        <div className="progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <small>{phase}</small>
    </div>
  );
}

function HistoryPage({ busy, currentProjectId, projects, onBack, onDelete, onOpen }) {
  return (
    <section className="history-page">
      <div className="history-toolbar">
        <button type="button" onClick={onBack}>
          <ArrowLeft size={18} />
          返回项目输入
        </button>
        <div>
          <h2>历史项目</h2>
          <p>{projects.length} 个项目</p>
        </div>
      </div>
      {projects.length === 0 ? (
        <EmptyState icon={<History size={28} />} title="暂无历史项目" />
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

function EmptyState({ icon, title }) {
  return (
    <div className="empty-state">
      {icon}
      <strong>{title}</strong>
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
