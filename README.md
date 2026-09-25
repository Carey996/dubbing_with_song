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

TTS 默认走 OpenRouter 的 OpenAI-compatible speech API，不再使用 PowerShell/System.Speech 降级：

```env
TTS_PROVIDER=openrouter
TTS_API_KEY=你的 OpenRouter API key
TTS_BASE_URL=https://openrouter.ai/api/v1
TTS_MODEL=openai/gpt-4o-mini-tts-2025-12-15
TTS_VOICE=alloy
TTS_SPEED=1.0
TTS_RESPONSE_FORMAT=mp3
```

OpenRouter 的 TTS 输出使用 MP3，后端会先保存 MP3，再用 `ffmpeg` 转成内部拼接需要的 WAV。当前 LM Studio 的本地 OpenAI-compatible 服务可用于文本模型，但不支持本项目需要的 `/v1/audio/speech` 方式，不能直接作为 TTS 服务。

后端默认会给 speech API 传一段“中文有声书旁白”风格的 `instructions`，让模型按语义调整语气、停顿和强弱，避免旁白过于平。需要换风格时可以在 `.env` 里配置：

```env
TTS_INSTRUCTIONS=用克制但带紧张感的悬疑旁白朗读，保留自然停顿和情绪起伏。
```

如果某个 TTS 服务不支持 instructions，或你想让模型只读原文，可以把它设成空白值关闭。

AI 分析会为每个片段补充 `speakerName`、`speakerGender`、`emotion`、`voiceStyle` 和 `delivery`。渲染时后端会把这些逐段写入 speech API 的配音提示；如果使用 Gemini TTS，还会把部分情绪转换成 inline style tags 放进输入文本。需要按男女声或角色区分 provider voice 时，可以配置：

```env
TTS_VOICE_MALE=onyx
TTS_VOICE_FEMALE=nova
TTS_VOICE_UNKNOWN=alloy
TTS_VOICE_POOL_MALE=onyx,echo
TTS_VOICE_POOL_FEMALE=nova,shimmer
```

如果配置了 `TTS_VOICE_POOL_*`，同一说话人会稳定映射到同一个 voice；未配置时按 `TTS_VOICE_MALE` / `TTS_VOICE_FEMALE` / `TTS_VOICE_UNKNOWN` 回退，再回到通用 `TTS_VOICE`。

如果正文是中文旁白，不要把 `TTS_MODEL` 配成 `mistralai/voxtral-*`。后端会在中文输入 + Voxtral TTS 组合下直接返回配置错误，避免等到 OpenRouter provider 侧报 `No successful provider responses`。

仓库不提交 `ffmpeg` 二进制文件。请自行安装 `ffmpeg` 并确保它位于系统 `PATH` 中，或者显式配置本地可执行文件路径：

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

后端启动时会初始化 SQLite，并扫描已有 `data/projects` 目录导入旧项目。刷新页面或重启服务后，可以从页面左侧历史项目列表重新打开项目，恢复文本、分析结果、时间轴、歌曲状态和最近一次生成结果。

## 当前能力

- 支持粘贴文本或上传 `.txt` 创建项目。
- LangChain LLM 分析器会拆分旁白/疑似歌词段，并给出歌曲候选提示。
- 独立章节切分 service 会先识别大段 txt 的章节，再按章节送入 LLM 分析。
- 支持上传单首 MP3 作为全篇 BGM。
- 支持在页面中调整段落类型、时长、BGM 音量和歌曲起点。
- 后端导出会优先通过 AI TTS 生成旁白 WAV。
- 如果系统安装了 `ffmpeg`，且项目上传了 MP3，后端会导出混音 MP3。
- 支持 `GET /api/projects/{id}/chapters` 预览章节，以及 `POST /api/projects/{id}/chapters/{chapter_id}/analyze` 按章节分析。

## 降级说明

当前 MVP 不负责在线下载音乐。歌曲文件由用户自行提供。

`POST /api/projects` 只接受 JSON：`{"text": "..."}`。txt 文件上传独立走 `POST /api/projects/text-file`，后端解码为文本后复用同一个项目创建服务入口。MP3 上传独立走 `POST /api/projects/{id}/song-file` 的 multipart 表单，避免创建接口同时兼容多种输入格式。

后端 TTS 必须配置可用的 AI speech API。没有 `TTS_API_KEY`、TTS 服务不可用，或 OpenRouter MP3 无法通过本机 `ffmpeg` 转成 WAV 时，渲染接口会直接返回错误，不再降级生成 PowerShell 语音或静音占位。

## 后续增强

- 接入更多本地 OpenAI-compatible TTS 服务。
- 支持多首歌、多角色配音和自动歌词时间轴对齐。
