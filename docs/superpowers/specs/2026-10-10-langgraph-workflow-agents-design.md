# LangGraph Workflow Agents + `/workflows` 页面设计

日期：2026-10-10
分支：`codex/langgraph-workflow-agents`
状态：已实施（见文末「实施结果」）

## 1. 目标

把 `dubbing-with-song` 现有的 7 个流程各改造为一个独立的 LangGraph `StateGraph`（下称 workflow agent），
统一由新增的 `/api/workflows/*` 接口暴露，并在前端新增 `/workflows` 页面供用户直接运行这些 agent 并实时观察每个节点的执行状态。

### 明确不做

- 不替换或改写现有的 REST 端点行为；现有 `input / chapters / analysis / history` 页面保持可用。
- 不做鉴权、多用户隔离、分布式执行、任务队列持久化重放。
- 不复制业务逻辑：workflow 节点只调用现有 `backend/app/services/*`。

### 验收口径

- `python -m pytest -q` 与 `npm test` 全绿（含全部既有测试与新增测试）。
- 前端 `npm run build` 成功。
- 手动在 `/workflows` 页面跑通 `project-intake`、`content-analysis`、`render` 三个流程；
  LLM/TTS 未配置时渲染必须返回结构化 `node_failed` 事件与可读中文错误，而不是 500 或静默降级。
- 页面刷新/后端重启后，已完成的 run 仍能在 `/workflows` 页面查到结果。

## 2. 七个工作流

每个工作流是一个独立 `StateGraph`，状态为 `WorkflowState`，节点通过公共装饰器记录 step 事件。

| # | id | 名称 | 节点链 | 条件边 / 分支 |
|---|---|---|---|---|
| 1 | `project-intake` | 项目接入 | `validate_text → create_project → preview_chapters` | 文本为空 → `fail` |
| 2 | `chapter-split` | 章节切分 | `load_project → split_chapters → summarize` | 无章节 → 单章 fallback |
| 3 | `content-analysis` | 全篇内容分析 | `load_project → split_chapters → build_chunks → llm_analyze → validate_json → merge_and_supplement → persist_analysis` | `validate_json` 解析失败 → `repair`（回环，最多 2 次）→ 仍失败 `fail` |
| 4 | `chapter-analysis` | 单章分析 | `resolve_chapter → llm_analyze_chapter → validate_json → maybe_apply_lrc → merge_into_timeline → persist` | 章节不存在 → `fail`；`validate_json` 失败 → `repair` 回环；存在 LRC → 走 enrich |
| 5 | `song-alignment` | 歌曲/歌词对齐 | `validate_assets → parse_lrc → match_segments → apply_cues → persist_timeline` | 无 MP3 → `fail`；无 LRC → 跳过匹配并 warning；低置信匹配 → `needsReview` warning |
| 6 | `timeline-edit` | 时间轴编辑保存 | `validate_timeline → normalize_segments → persist_timeline` | 片段列表为空 → `fail` |
| 7 | `render` | 渲染导出 | `load_timeline → resolve_mix_source → build_narration_track → 混音/仅旁白 → persist_render_record` | 有歌+ffmpeg → `mixed`；无 ffmpeg → `narration-only` + warning；歌词片段缺音源 → `fail`；无分析/时间轴 → `fail` |

「agent」能力集中在 #3/#4 的 **LLM 节点 + 自修复回环** 与 #7 的 **可行性条件路由**；其余节点为确定性编排。

## 3. 后端结构

新增（不修改既有业务文件，除 `main.py` 挂载 router 与 `pyproject.toml` 加依赖）：

```
backend/app/workflows/__init__.py
backend/app/workflows/state.py            # WorkflowState / StepRecord / RunEvent
backend/app/workflows/common.py           # step 装饰器、错误归类、事件构造
backend/app/workflows/registry.py         # 7 个工作流元数据 + 输入 schema + graph 工厂
backend/app/workflows/project_intake.py
backend/app/workflows/chapter_split.py
backend/app/workflows/content_analysis.py
backend/app/workflows/chapter_analysis.py
backend/app/workflows/song_alignment.py
backend/app/workflows/timeline_edit.py
backend/app/workflows/render.py
backend/app/workflows/runner.py           # 运行注册表、线程执行、事件总线、持久化
backend/app/routers/workflows.py          # /api/workflows/*
tests/test_workflows.py
```

依赖：`pyproject.toml` 增加 `langgraph>=1.0`，用 `uv lock` 同步 `uv.lock`。
现有 `.venv` 位于主 checkout，worktree 内用 `uv sync` 建立自己的 `.venv`（`.venv/` 已被 gitignore）。

### 工作流状态

```python
class StepRecord(TypedDict):
    node: str          # 节点函数名
    label: str         # 中文可读标签
    status: str        # running | succeeded | failed | skipped
    startedAt: str
    finishedAt: str | None
    detail: str        # 结果摘要
    elapsedMs: int | None

class WorkflowState(TypedDict, total=False):
    runId: str
    workflowId: str
    projectId: str
    inputs: dict
    steps: list[StepRecord]
    data: dict          # 节点间传递的中间数据
    warnings: list[str]
    result: dict        # 最终返回给前端的结构化结果
    error: str | None
```

`WorkflowState` 走 LangGraph 的 channel 合并语义：`steps`、`warnings` 用追加归约，其余字段整体覆盖。

### 输入 schema

`registry.py` 为每个工作流声明输入参数，供前端动态渲染表单并做前端校验：

```python
@dataclass(frozen=True)
class WorkflowField:
    name: str
    label: str
    type: str            # text | textarea | number | select | project | chapter | boolean
    required: bool = False
    default: Any = None
    options: list[dict] | None = None
    help: str = ""
```

校验在后端执行（前端校验只是体验优化）；非法参数返回 422 或让 graph 进入 `fail` 分支。

## 4. 运行与事件模型（SSE）

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/workflows` | 7 个工作流元数据 + 输入 schema |
| POST | `/api/workflows/{workflow_id}/runs` | JSON 参数，创建并启动 run，返回 `{runId, workflowId, status}` |
| GET | `/api/workflows/runs` | 最近 run 列表（持久化读取） |
| GET | `/api/workflows/runs/{run_id}` | run 状态快照（刷新页面后恢复） |
| GET | `/api/workflows/runs/{run_id}/events` | SSE 事件流，支持 `Last-Event-ID` 重放 |

文件上传（txt / mp3 / lrc）继续走现有上传端点：用户在页面里先上传，再把 `projectId` 交给 workflow。

### 事件

事件类型：`run_started`、`node_started`、`node_finished`、`node_failed`、`log`、`warning`、`run_finished`、`run_failed`。

```
id: 12
event: node_finished
data: {"seq":12,"runId":"...","workflowId":"content-analysis","node":"llm_analyze","label":"LLM 分析","status":"succeeded","elapsedMs":4210,"detail":"解析出 18 个片段"}
```

- `seq` 从 1 单调递增，是 `Last-Event-ID` 重放依据。
- 每 15s 发送 SSE 注释行 `: ping` 作心跳。
- 终态事件（`run_finished` / `run_failed`）之后服务端关闭流。

### 执行模型

- run 状态机：`queued → running → succeeded | failed`。
- `graph.stream(stream_mode="updates")` 在后台线程执行；事件通过 `loop.call_soon_threadsafe` 投递到每个订阅者的 `asyncio.Queue`。
- 并发上限 4（`threading.Semaphore`），超出上限的 run 保持 `queued`。
- run 记录（含完整事件序列与最终结果）持久化到 `data/workflow_runs/<run_id>.json`；
  服务重启后 `GET /api/workflows/runs` 与状态快照仍可读，SSE 可对已结束 run 直接重放全部事件。

## 5. 前端结构

新增：

- `frontend/src/workflowCatalog.js`：工作流展示元数据、默认参数、参数校验、结果摘要格式化（纯函数）。
- `frontend/src/workflowRunStream.js`：SSE 客户端 + 事件归约为 UI 状态（纯 reducer，可单测）。
- `frontend/src/WorkflowPage.jsx`：工作流页面（工作流选择 → 参数表单 → 运行 → 节点时间线 → 结果面板）。

修改：

- `frontend/src/workflowNavigation.js`：新增 `workflows` 页定义与 `/workflows` 路由解析/构建。
- `frontend/src/App.jsx`：仅新增导航入口与 `<WorkflowPage />` 渲染，不新增业务逻辑。
- `frontend/src/styles.css`：工作流页面样式。

页面行为：

- 左侧工作流列表（7 项，按数据流顺序分组：接入 → 切分 → 分析 → 对齐 → 时间轴 → 渲染）。
- 选中后显示参数表单；`project` 类型字段从 `GET /api/projects` 拉取下拉选项，`chapter` 类型字段依赖所选项目的 `GET /api/projects/{id}/chapters`。
- 运行时展示节点时间线：每个节点显示状态图标、耗时、`detail`；失败节点标红并展示错误。
- 结束后展示结果面板：项目链接、章节数/片段数/歌词数、渲染产物音频播放器与下载链接。
- 历史 run 列表可点开回放（读取已持久化的事件序列）。

## 6. 测试

后端新增 `tests/test_workflows.py`：

- 7 个 graph 各自的成功路径与失败/条件分支（monkeypatch 现有 service 与 LLM/TTS，不触网）。
- `content-analysis` / `chapter-analysis` 的 JSON 修复回环：第一次返回坏 JSON，第二次返回好 JSON，断言只回环一次。
- registry 元数据完整性：7 个 id 唯一、每个字段类型合法。
- runner：事件 `seq` 单调、终态正确、并发上限生效、run 持久化后重新加载可读。
- 接口层：`GET /api/workflows`、创建 run、状态快照、SSE 输出为合法 `text/event-stream`，
  `Last-Event-ID` 重放只返回后续事件。
- 既有测试全部保持通过（不改旧测试文件）。

前端新增/扩充：

- `frontend/src/workflowCatalog.test.mjs`
- `frontend/src/workflowRunStream.test.mjs`（事件归约、乱序/重复 seq、失败终态）
- `frontend/src/workflowNavigation.test.mjs`（新增 `/workflows` 路由用例）

## 7. 风险与取舍

| 风险 | 处理 |
|---|---|
| LangGraph 需联网安装 | 已验证 pypi 可达、`uv` 可用；worktree 内独立 `.venv` |
| LLM/TTS 未配置导致工作流失败 | 预期行为；统一转为 `node_failed` 事件与可读中文错误，不静默降级 |
| `App.jsx` 已 1724 行 | 新页面独立文件，`App.jsx` 只加导航与渲染 |
| SSE 与同步 service 混用 | 后台线程执行 graph，`call_soon_threadsafe` 投递事件；SSE 端点为 `async def` |
| 运行记录增长 | 保留最近 `WORKFLOW_RUN_KEEP_COUNT`（默认 50）个 run 文件 |

## 8. 工作区

按 `AGENTS.md` 在 `.worktrees/langgraph-workflow-agents` 的 `codex/langgraph-workflow-agents` 分支实施；
完成后运行验证，再询问是否合并回 `main`。

## 9. 实施结果

- 后端：`backend/app/workflows/`（state/common/registry/runner/analysis_nodes + 7 个 graph）与
  `backend/app/routers/workflows.py`；`main.py` 只增加一行 router 挂载。
- `analyzer.py` 做了一次保持行为的抽取，新增 `build_analyzer_llm()` / `analyzer_chunk_chars()`，
  让分析工作流复用同一套 prompt 与模型配置，而不是复制一份。
- 前端：`WorkflowPage.jsx`、`workflowCatalog.js`、`workflowRunStream.js`、`api.js`；
  `workflowNavigation.js` 把 `workflows` 作为与 `history` 并列的独立路由，项目三步导航不变。
- 依赖：`langgraph>=1.0`（实际安装 1.2.14，与 `langchain-core` 1.6 兼容）。

验证证据（全部在 worktree 内执行）：

| 验证 | 结果 |
| --- | --- |
| `python -m pytest -q` | 138 passed（既有 79 + 新增 59） |
| `npm test` | 9 个测试文件全部通过（新增 `workflowCatalog` / `workflowRunStream` / 扩充 `workflowNavigation`） |
| `npm run build` | 构建成功 |
| 真实浏览器（Chrome + 本地后端/Vite） | `/workflows` 列出 7 个工作流；`project-intake`、`chapter-split`、`timeline-edit` 跑通并正确落库；`content-analysis` 在缺少 `OPENAI_API_KEY` 时显示 `node_failed`（节点 `llm_analyze`）而不是空白错误；运行记录可回放 |

未覆盖：LLM/TTS 真实调用路径（本机无 key）——工作流对这类失败的行为已由 mock 测试与浏览器验证覆盖。

