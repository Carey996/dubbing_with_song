# 本地 AI 配音 + 歌曲提示 MVP

这个仓库实现一个本地网页工作流：上传 txt 或粘贴文本，自动识别旁白和疑似歌词片段，提示可能的歌曲信息，用户上传自己的 MP3 后生成旁白和歌曲混合结果。

## 运行

先创建 `.env`：

```powershell
Copy-Item .env.example .env
```

默认 `.env.example` 指向 LM Studio：

```env
OPENAI_API_KEY=lm-studio
OPENAI_BASE_URL=http://127.0.0.1:1234/v1
OPENAI_MODEL=gemma-4-e4b-it@q4_k_m
```

如果换成 OpenAI 官方服务，把 `OPENAI_BASE_URL` 删除或改成官方兼容地址，并把 `OPENAI_API_KEY` 改成真实 key。文本分析只走 LangChain LLM；没有 `OPENAI_API_KEY` 会直接报配置错误。

后端：

```powershell
python -m uvicorn backend.app.main:app --reload --port 28080
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

打开 Vite 输出的本地地址，默认会把 `/api` 代理到 `http://127.0.0.1:28080`。

本地 CLI：

```powershell
python -m backend.app.cli .\story.txt
python -m backend.app.cli .\story.txt --song .\song.mp3 --render
python -m backend.app.cli .\story.txt --chapters-only
```

CLI 会读取 txt，然后复用 Web API 背后的同一套项目创建、章节切分、分析、上传歌曲和渲染 service。默认会创建项目并执行章节切分与文本分析；传 `--chapters-only` 时只输出章节切分结果，传 `--no-analyze` 时创建项目并输出章节但不调用 LLM。

## 当前能力

- 支持粘贴文本或上传 `.txt` 创建项目。
- LangChain LLM 分析器会拆分旁白/疑似歌词段，并给出歌曲候选提示。
- 独立章节切分 service 会先识别大段 txt 的章节，再按章节送入 LLM 分析。
- 支持上传单首 MP3 作为全篇 BGM。
- 支持在页面中调整段落类型、时长、BGM 音量和歌曲起点。
- 后端导出会优先生成旁白 WAV。
- 如果系统安装了 `ffmpeg`，且项目上传了 MP3，后端会导出混音 MP3。

## 降级说明

当前 MVP 不负责在线下载音乐。歌曲文件由用户自行提供。

`POST /api/projects` 只接受 JSON：`{"text": "..."}`。txt 文件上传独立走 `POST /api/projects/text-file`，后端解码为文本后复用同一个项目创建服务入口。MP3 上传独立走 `POST /api/projects/{id}/song-file` 的 multipart 表单，避免创建接口同时兼容多种输入格式。

后端 TTS 在 Windows 上优先使用 `System.Speech` 生成 WAV；如果不可用，会生成静音占位音频以保证接口流程可走通。没有 `ffmpeg` 时无法在后端混入 MP3，但前端仍可上传、预览、编辑时间轴，并导出旁白版本。

## 后续增强

- 接入真实 LLM 替换当前启发式分析器。
- 接入更高质量 TTS 服务。
- 支持多首歌、多角色配音和自动歌词时间轴对齐。
