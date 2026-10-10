# 本地 AI 配音 + 歌曲提示 MVP

这个仓库实现一个本地网页工作流：上传 txt 或粘贴文本，自动识别旁白和疑似歌词片段，提示可能的歌曲信息，用户上传自己的 MP3 后生成旁白和歌曲混合结果。

## 运行

先创建 `.env`：

```bash
cp .env.example .env
```

默认 `.env.example` 指向 LM Studio：

```env
OPENAI_API_KEY=lm-studio
OPENAI_BASE_URL=http://127.0.0.1:1234/v1
OPENAI_MODEL=gemma-4-e4b-it@q4_k_m
```

如果换成 OpenAI 官方服务，把 `OPENAI_BASE_URL` 删除或改成官方兼容地址，并把 `OPENAI_API_KEY` 改成真实 key。文本分析只走 LangChain LLM；没有 `OPENAI_API_KEY` 会直接报配置错误。

语音合成只走 MiniMax 的 `POST /v1/t2a_v2`，没有 provider 开关，也不再使用 PowerShell/System.Speech 降级：

```env
TTS_API_KEY=你的 MiniMax key
TTS_BASE_URL=https://api.minimax.cn/v1
TTS_MODEL=speech-2.8-hd
TTS_VOICE=female-yujie
TTS_SPEED=1.0
TTS_RESPONSE_FORMAT=wav
```

同一个 MiniMax key 可以同时用于文本分析和语音：MiniMax 的文本接口兼容 OpenAI，所以 LLM 侧只要换 base URL 和模型名，无需改代码。

```env
OPENAI_API_KEY=sk-...同一个 key...
OPENAI_BASE_URL=https://api.minimax.cn/v1
OPENAI_MODEL=MiniMax-M3.1-Flash-Preview
```

要点：

- **base URL 必须是国内站**（`api.minimax.cn` 或 `api.minimaxi.com`）。拿同一个 key 打 `api.minimax.io`（国际站）会返回 `status_code 2049 invalid api key`。
- `TTS_RESPONSE_FORMAT=wav`（默认）时 MiniMax 直出 **22.05 kHz 单声道 16bit WAV**，正好是内部旁白拼接的格式，**旁白路径完全不需要 ffmpeg**；设成 `mp3` 则需要 ffmpeg 转码。
- MiniMax **没有自由文本 instructions 字段**（`TTS_INSTRUCTIONS` 已随之移除）。情绪改由 `voice_setting.emotion` 传递：分析器给出的 `gentle→calm`、`excited→happy`、`angry/sad/fearful/happy` 直传，`neutral/tense` 不传（让模型自己判断）；`speech-2.8` 不支持 `whisper`，后端会自动丢弃该值而不是让请求报错。
- 音色要用 MiniMax 的音色 id（`female-yujie`、`male-qn-jingying`、`female-chengshu`、`female-shaonv` 等，共 303 个系统音色），`alloy` 这类 OpenAI 音色名无效。`TTS_SPEED` 会被夹到 MiniMax 允许的 `[0.5, 2]`。
- 需要强制语种时设 `TTS_LANGUAGE_BOOST=Chinese`（默认 `auto`，由 MiniMax 自动识别）。
- MiniMax 把限流作为业务错误返回（`1002` rate limit、`1039` TPM rate limit），`1004` 表示鉴权失败。渲染会按 `TTS_MAX_ATTEMPTS`（默认 3）做指数退避重试，间隔由 `TTS_RETRY_BASE_DELAY`（默认 1 秒，上限 30 秒）控制，单次请求超时用 `TTS_TIMEOUT_SECONDS`（默认 60）；鉴权与参数类错误不重试，失败信息会带上当前配置。

```env
TTS_LANGUAGE_BOOST=Chinese
TTS_MAX_ATTEMPTS=3
TTS_RETRY_BASE_DELAY=1.0
TTS_TIMEOUT_SECONDS=60
```

AI 分析会为每个片段补充 `speakerName`、`speakerGender`、`emotion`、`voiceStyle` 和 `delivery`。渲染时 `emotion` 会映射成 MiniMax 的 `voice_setting.emotion`；需要按男女声或角色区分音色时，可以配置：

```env
TTS_VOICE_MALE=male-qn-jingying
TTS_VOICE_FEMALE=female-yujie
TTS_VOICE_UNKNOWN=female-chengshu
TTS_VOICE_POOL_MALE=male-qn-qingse,male-qn-badao
TTS_VOICE_POOL_FEMALE=female-shaonv,female-tianmei
```

如果配置了 `TTS_VOICE_POOL_*`，同一说话人会稳定映射到同一个 voice；未配置时按 `TTS_VOICE_MALE` / `TTS_VOICE_FEMALE` / `TTS_VOICE_UNKNOWN` 回退，再回到通用 `TTS_VOICE`。

仓库不提交 `ffmpeg` 二进制文件。请自行安装 `ffmpeg` 并确保它位于系统 `PATH` 中，或者显式配置本地可执行文件路径。
`TTS_RESPONSE_FORMAT=wav` 时旁白路径不需要 ffmpeg，但歌词片段卡点混音仍然需要：

```env
FFMPEG_PATH=D:\path\to\ffmpeg.exe
```

后端：

```bash
python -m uvicorn backend.app.main:app --reload --port 28080
```

前端：

```bash
cd frontend
npm install
npm run dev
```

打开 Vite 输出的本地地址，默认会把 `/api` 代理到 `http://127.0.0.1:28080`。

测试：

```bash
python -m pytest -q          # 后端；数据写在临时目录，不会碰本地 data/
npm test                     # 仓库根目录，等价于 cd frontend && npm test
```

本地 CLI：

```bash
python -m backend.app.cli ./story.txt
python -m backend.app.cli ./story.txt --song ./song.mp3 --render
python -m backend.app.cli ./story.txt --chapters-only
python -m backend.app.cli ./story.txt --chapter chap-002
```

CLI 会读取 txt，然后复用 Web API 背后的同一套项目创建、章节切分、分析、上传歌曲和渲染 service。默认会创建项目并执行章节切分与全篇文本分析；传 `--chapter chap-002` 时只分析指定章节，传 `--chapters-only` 时只输出章节切分结果，传 `--no-analyze` 时创建项目并输出章节但不调用 LLM。

## 本地持久化

项目使用 SQLite + 本地文件资产保存完整流程状态：

- `data/app.sqlite3` 保存项目列表、分析历史、渲染历史和资产索引。
- `backend/app/repositories/sql/*.sql` 保存建表和查询语句，运行时由 repository 层读取并绑定参数执行。
- `data/projects/<project_id>/` 保存原始文本、上传 MP3、分析 JSON 和时间轴 JSON。
- `data/outputs/<project_id>/` 保存生成的音频文件；每次渲染会进入独立的 render 目录。

每次成功渲染后，只保留最近 `RENDER_KEEP_COUNT`（默认 3）次**成功**渲染的音频文件和渲染历史，更早的产物和失败尝试的残留会被清理；`latestRender` 指向的那次渲染永远不会被清理。

后端启动时会初始化 SQLite，并扫描已有 `data/projects` 目录导入旧项目。刷新页面或重启服务后，可以从页面左侧历史项目列表重新打开项目，恢复文本、分析结果、时间轴、歌曲状态和最近一次生成结果。

存储根目录默认是仓库下的 `data/`，可以用环境变量 `DUBBING_DATA_DIR` 覆盖（测试套件用它把 SQLite 和项目目录指向临时目录，所以跑 `pytest` 不会写进本地数据）：

```bash
DUBBING_DATA_DIR=/tmp/dubbing-data python -m uvicorn backend.app.main:app --reload --port 28080
```

渲染产物保留数量同样可以用环境变量覆盖：

```bash
RENDER_KEEP_COUNT=5 python -m uvicorn backend.app.main:app --reload --port 28080
```

## 当前能力

- 支持粘贴文本或上传 `.txt` 创建项目。
- LangChain LLM 分析器会拆分旁白/疑似歌词段，并给出歌曲候选提示。
- 独立章节切分 service 会先识别大段 txt 的章节，再按章节送入 LLM 分析。
- 支持上传单首 MP3 作为全篇 BGM。
- 支持在页面中调整段落类型、时长、BGM 音量、旁白音量（混音时生效）和歌曲起点。
- 后端导出会优先通过 AI TTS 生成旁白 WAV。
- 如果系统安装了 `ffmpeg`，且项目上传了 MP3，后端会导出混音 MP3。
- 支持 `GET /api/projects/{id}/chapters` 预览章节，以及 `POST /api/projects/{id}/chapters/{chapter_id}/analyze` 按章节分析。
- 每个流程都额外提供一个 LangGraph 工作流 Agent，可在 `/workflows` 页面单独运行并实时查看每个节点的状态。

## LangGraph 工作流 Agent

`backend/app/workflows/` 把上面的 7 个流程各包装成一个 LangGraph `StateGraph`。节点只调用现有
`backend/app/services/*`，不复制业务逻辑；`content-analysis` 和 `chapter-analysis` 是真正的
agent 循环：LLM 返回的 JSON 解析失败会自动重试（最多 3 次），仍然失败才终止；`render` 会根据
是否有 MP3 和 `ffmpeg` 走混音或仅旁白分支。

| workflow id | 名称 | 说明 |
| --- | --- | --- |
| `project-intake` | 项目接入 | 校验文本 → 创建项目 → 预览章节 |
| `chapter-split` | 章节切分 | 读取项目 → 按标题切分 → 输出概览 |
| `content-analysis` | 全篇内容分析 | 分块调用 LLM，JSON 解析失败自动重试 |
| `chapter-analysis` | 单章分析 | 只分析一章并并回时间轴，有 LRC 时自动写入卡点 |
| `song-alignment` | 歌曲/歌词对齐 | 用 LRC 匹配歌词片段并写入歌曲卡点 |
| `timeline-edit` | 时间轴保存 | 校验 + 归一化片段后写入 `timeline.json` |
| `render` | 渲染导出 | 生成旁白音轨，能混音时输出 MP3，否则导出 WAV |

### 页面

打开 `/workflows`（或在顶部点「工作流」）。左侧选工作流，右侧填参数后运行：节点时间线会实时显示
每个节点的状态、耗时和结果摘要，结束后展示结构化结果（项目/分析跳转、渲染产物播放与下载），
下方是最近 50 次运行记录，可点开回放。

### 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/workflows` | 7 个工作流的元数据与输入 schema（前端据此渲染表单） |
| `POST` | `/api/workflows/{workflow_id}/runs` | 提交参数启动运行，返回 `{runId, status}`（202） |
| `GET` | `/api/workflows/runs` | 最近的运行记录 |
| `GET` | `/api/workflows/runs/{run_id}` | 运行快照（含已产生的事件，刷新页面后恢复用） |
| `GET` | `/api/workflows/runs/{run_id}/events` | SSE 事件流，支持 `Last-Event-ID` 重放 |

事件类型为 `run_started`、`node_started`、`node_finished`、`node_failed`、`warning`、
`run_finished`、`run_failed`，每条事件带单调递增的 `seq`。文件上传仍然走原有的项目接口
（先在「项目」页上传 MP3 / LRC / txt），workflow 只接受 JSON 参数。

运行记录保存在 `data/workflow_runs/<run_id>.json`，保留最近 `WORKFLOW_RUN_KEEP_COUNT`（默认 50）个；
并发运行上限由 `WORKFLOW_RUN_CONCURRENCY`（默认 4）控制，超出上限的 run 保持 `queued`。
后端重启时未完成的 run 会被标记为 `interrupted`。

```bash
WORKFLOW_RUN_CONCURRENCY=2 WORKFLOW_RUN_KEEP_COUNT=20 python -m uvicorn backend.app.main:app --reload --port 28080
```

## 降级说明

当前 MVP 不负责在线下载音乐。歌曲文件由用户自行提供。

`POST /api/projects` 只接受 JSON：`{"text": "..."}`。txt 文件上传独立走 `POST /api/projects/text-file`，后端解码为文本后复用同一个项目创建服务入口。MP3 上传独立走 `POST /api/projects/{id}/song-file` 的 multipart 表单，避免创建接口同时兼容多种输入格式。

后端 TTS 必须配置可用的 MiniMax speech API。没有 `TTS_API_KEY`、key 无效（`1004`）、触发限流且重试耗尽，或 `TTS_RESPONSE_FORMAT=mp3` 时本机缺少 `ffmpeg`，渲染接口都会直接返回带配置上下文的错误，不再降级生成 PowerShell 语音或静音占位。

## 后续增强

- 如果以后要接 MiniMax 之外的 TTS 服务，需要重新引入 provider 抽象（当前代码只实现了 MiniMax 协议）。
- 支持多首歌、多角色配音和自动歌词时间轴对齐。
