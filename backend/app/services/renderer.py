from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

from fastapi import HTTPException

from .project_store import Project, read_timeline


def render_project(project: Project) -> dict:
    timeline = read_timeline(project)
    render_dir = project.output_dir
    chunks_dir = render_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    narration_path = render_dir / "narration.wav"
    build_narration_track(timeline, chunks_dir, narration_path, include_lyrics=not project.song_path.exists())

    ffmpeg = shutil.which("ffmpeg")
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
    script_path = output_path.with_suffix(".ps1")
    input_path = output_path.with_suffix(".txt")
    input_path.write_text(text or " ", encoding="utf-8")
    script_path.write_text(
        """
Add-Type -AssemblyName System.Speech
$text = Get-Content -LiteralPath $args[0] -Raw -Encoding UTF8
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.Rate = 0
$speaker.Volume = 100
$speaker.SetOutputToWaveFile($args[1])
$speaker.Speak($text)
$speaker.Dispose()
""".strip(),
        encoding="utf-8",
    )

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell:
        result = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path), str(input_path), str(output_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and output_path.exists():
            return

    create_silence_wav(output_path, estimate_spoken_duration(text), (1, 2, 22050))


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
