from __future__ import annotations

import os
import shutil
import subprocess
import wave
from pathlib import Path

from fastapi import HTTPException
from openai import OpenAI

from .project_store import Project, read_timeline

BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_TTS_VOICE = "alloy"
DEFAULT_OPENROUTER_TTS_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_TTS_MODEL = "openai/gpt-4o-mini-tts-2025-12-15"
BUNDLED_FFMPEG_PATH = BASE_DIR / "tools" / "ffmpeg" / "windows-x64" / "ffmpeg.exe"


def render_project(project: Project) -> dict:
    timeline = read_timeline(project)
    render_dir = project.output_dir
    chunks_dir = render_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    narration_path = render_dir / "narration.wav"
    build_narration_track(timeline, chunks_dir, narration_path, include_lyrics=not project.song_path.exists())

    ffmpeg = find_ffmpeg()
    if project.song_path.exists() and ffmpeg:
        output_path = render_dir / "mixed.mp3"
        mix_with_song(ffmpeg, narration_path, project.song_path, output_path, timeline)
        return {
            "status": "mixed",
            "message": "已生成旁白 + MP3 BGM 混音。",
            "outputUrl": f"/outputs/{project.id}/{output_path.name}",
            "warnings": [],
        }

    warnings = []
    if project.song_path.exists() and not ffmpeg:
        warnings.append("未检测到 ffmpeg，后端无法混入 MP3，已导出旁白 WAV。")
    return {
        "status": "narration-only",
        "message": "已生成旁白 WAV。",
        "outputUrl": f"/outputs/{project.id}/{narration_path.name}",
        "warnings": warnings,
    }


def build_narration_track(timeline: dict, chunks_dir: Path, output_path: Path, include_lyrics: bool) -> None:
    segments = timeline.get("segments") or []
    chunk_paths: list[Path] = []
    sample_template: tuple[int, int, int] | None = None

    for segment in segments:
        text = segment.get("text", "")
        kind = segment.get("type", "narration")
        duration = float(segment.get("durationSec", 2.0) or 2.0)
        chunk_path = chunks_dir / f"{segment.get('id', len(chunk_paths))}.wav"

        if kind == "lyric" and not include_lyrics:
            if sample_template is None:
                sample_template = (1, 2, 22050)
            create_silence_wav(chunk_path, duration, sample_template)
        else:
            synthesize_wav(text, chunk_path)
            with wave.open(str(chunk_path), "rb") as wav:
                sample_template = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())

        chunk_paths.append(chunk_path)

    if not chunk_paths:
        create_silence_wav(output_path, 1.0, (1, 2, 22050))
        return

    concatenate_wavs(chunk_paths, output_path)


def synthesize_wav(text: str, output_path: Path) -> None:
    provider = os.getenv("TTS_PROVIDER", "openrouter").strip().lower()
    if provider == "openrouter":
        synthesize_wav_with_openrouter(text, output_path)
        return
    if provider == "openai":
        synthesize_wav_with_openai(text, output_path)
        return

    raise HTTPException(status_code=503, detail=f"Unsupported TTS_PROVIDER: {provider}. Use openrouter or openai.")


def synthesize_wav_with_openai(text: str, output_path: Path) -> None:
    synthesize_audio_with_openai_compatible(
        text=text,
        output_path=output_path,
        default_base_url=None,
        default_model=DEFAULT_TTS_MODEL,
        response_format="wav",
    )


def synthesize_wav_with_openrouter(text: str, output_path: Path) -> None:
    mp3_path = output_path.with_suffix(".mp3")
    response_format = os.getenv("TTS_RESPONSE_FORMAT", "mp3").strip().lower()
    if response_format != "mp3":
        raise HTTPException(status_code=503, detail="OpenRouter TTS currently requires TTS_RESPONSE_FORMAT=mp3 so ffmpeg can convert it to WAV.")

    synthesize_audio_with_openai_compatible(
        text=text,
        output_path=mp3_path,
        default_base_url=DEFAULT_OPENROUTER_TTS_BASE_URL,
        default_model=DEFAULT_OPENROUTER_TTS_MODEL,
        response_format=response_format,
    )

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="Missing ffmpeg. OpenRouter TTS returns MP3, so ffmpeg is required to convert it to WAV.")
    convert_audio_to_wav(ffmpeg, mp3_path, output_path)

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(status_code=502, detail="OpenRouter TTS conversion returned an empty WAV file.")


def synthesize_audio_with_openai_compatible(
    text: str,
    output_path: Path,
    default_base_url: str | None,
    default_model: str,
    response_format: str,
) -> None:
    api_key = os.getenv("TTS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Missing required TTS config: TTS_API_KEY. Set it in .env.")

    client_kwargs = {"api_key": api_key}
    base_url = os.getenv("TTS_BASE_URL") or default_base_url
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)
    try:
        response = client.audio.speech.create(
            model=os.getenv("TTS_MODEL", default_model),
            voice=os.getenv("TTS_VOICE", DEFAULT_TTS_VOICE),
            input=text or " ",
            response_format=response_format,
            speed=float(os.getenv("TTS_SPEED", "1.0")),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        response.write_to_file(output_path)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI TTS failed: {exc}") from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(status_code=502, detail="AI TTS returned an empty audio file.")


def find_ffmpeg() -> str | None:
    configured = os.getenv("FFMPEG_PATH")
    if configured and Path(configured).is_file():
        return configured

    if BUNDLED_FFMPEG_PATH.is_file():
        return str(BUNDLED_FFMPEG_PATH)

    executable = shutil.which("ffmpeg")
    if executable:
        return executable

    return None


def convert_audio_to_wav(ffmpeg: str, input_path: Path, output_path: Path) -> None:
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "22050",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg audio conversion failed: {result.stderr[-800:]}")


def create_silence_wav(path: Path, duration: float, params: tuple[int, int, int]) -> None:
    channels, sample_width, frame_rate = params
    frame_count = max(1, int(duration * frame_rate))
    silence_frame = b"\x00" * sample_width * channels
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(frame_rate)
        wav.writeframes(silence_frame * frame_count)


def concatenate_wavs(paths: list[Path], output_path: Path) -> None:
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()

    with wave.open(str(output_path), "wb") as output:
        output.setparams(params)
        for path in paths:
            with wave.open(str(path), "rb") as wav:
                if wav.getparams()[:3] != params[:3]:
                    raise HTTPException(status_code=500, detail="TTS chunks have incompatible WAV formats.")
                output.writeframes(wav.readframes(wav.getnframes()))


def mix_with_song(ffmpeg: str, narration_path: Path, song_path: Path, output_path: Path, timeline: dict) -> None:
    duration = get_wav_duration(narration_path)
    bgm_volume = float(timeline.get("bgmVolume", 0.22) or 0.22)
    song_start = float(timeline.get("songStartSec", 0.0) or 0.0)
    filter_graph = (
        f"[1:a]volume={bgm_volume},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[bgm];"
        "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[out]"
    )
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(narration_path),
        "-stream_loop",
        "-1",
        "-ss",
        f"{song_start:.3f}",
        "-i",
        str(song_path),
        "-filter_complex",
        filter_graph,
        "-map",
        "[out]",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "3",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg render failed: {result.stderr[-800:]}")


def get_wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def estimate_spoken_duration(text: str) -> float:
    return max(1.0, len("".join((text or "").split())) / 6.5)
