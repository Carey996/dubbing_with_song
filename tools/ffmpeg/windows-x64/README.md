# Bundled ffmpeg

This directory contains the Windows x64 ffmpeg runtime used by the backend when `FFMPEG_PATH` is not set.

Version checked locally:

```powershell
.\tools\ffmpeg\windows-x64\ffmpeg.exe -version
```

Current binary reports `ffmpeg version n7.0.2` and includes `libmp3lame`, which the renderer needs for MP3 mixing.
