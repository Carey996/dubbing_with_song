from __future__ import annotations

import math
import os
import shutil
import subprocess
import wave
from pathlib import Path

from fastapi import HTTPException

from . import minimax_tts
from .project_store import Project, read_timeline

BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_TTS_VOICE = "female-yujie"
BUNDLED_FFMPEG_PATH = BASE_DIR / "tools" / "ffmpeg" / "windows-x64" / "ffmpeg.exe"


def render_project(project: Project) -> dict:
    timeline = read_timeline(project)
    has_song = project.song_path.exists()
    has_lyrics = has_lyric_segments(timeline)
    lyric_song_paths = resolve_lyric_segment_song_paths(project, timeline) if has_lyrics else {}
    has_mix_source = has_song or bool(lyric_song_paths)

    render_dir = project.output_dir
    chunks_dir = render_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    narration_path = render_dir / "narration.wav"
    build_narration_track(timeline, chunks_dir, narration_path, include_lyrics=not has_lyrics)

    ffmpeg = find_ffmpeg()
    if has_lyrics and not ffmpeg:
        raise HTTPException(status_code=503, detail="歌词片段需要 ffmpeg 才能从 MP3 裁切混音。")

    if has_mix_source and ffmpeg:
        output_path = render_dir / "mixed.mp3"
        default_song_path = project.song_path if has_song else next(iter(lyric_song_paths.values()))
        mix_with_song(ffmpeg, narration_path, default_song_path, output_path, timeline, lyric_song_paths=lyric_song_paths)
        return {
            "status": "mixed",
            "message": "已生成旁白 + MP3 BGM 混音。",
            "outputUrl": project.output_url(output_path),
            "warnings": [],
        }

    warnings = []
    if has_mix_source and not ffmpeg:
        warnings.append("未检测到 ffmpeg，后端无法混入 MP3，已导出旁白 WAV。")
    return {
        "status": "narration-only",
        "message": "已生成旁白 WAV。",
        "outputUrl": project.output_url(narration_path),
        "warnings": warnings,
    }


def build_narration_track(timeline: dict, chunks_dir: Path, output_path: Path, include_lyrics: bool) -> None:
    segments = timeline.get("segments") or []
    chunk_paths: list[Path] = []
    fallback_params = build_default_sample_template()

    # Lyric placeholders must share the narrator track's WAV format, otherwise
    # concatenate_wavs rejects the render. AI speech providers return their own format
    # (OpenAI-compatible models return 24 kHz PCM), so when a segment that is not sent
    # to TTS comes first, the format is probed from the first synthesized segment before
    # any placeholder silence is written.
    sample_template = None
    if needs_format_probe(segments, include_lyrics):
        sample_template = detect_sample_template(segments, chunks_dir) or fallback_params

    for segment in segments:
        text = segment.get("text", "")
        kind = segment.get("type", "narration")
        duration = float(segment.get("durationSec", 2.0) or 2.0)
        chunk_path = chunks_dir / f"{segment.get('id', len(chunk_paths))}.wav"

        if kind == "lyric" and not include_lyrics:
            create_silence_wav(chunk_path, duration, sample_template or fallback_params)
        else:
            try:
                synthesize_wav(text, chunk_path, segment=segment)
            except HTTPException as exc:
                raise wrap_segment_tts_failure(segment, exc) from exc
            chunk_params = read_wav_params(chunk_path)
            if chunk_params:
                sample_template = chunk_params

        chunk_paths.append(chunk_path)

    if not chunk_paths:
        create_silence_wav(output_path, 1.0, fallback_params)
        return

    concatenate_wavs(chunk_paths, output_path)


def needs_format_probe(segments: list[dict], include_lyrics: bool) -> bool:
    if not segments:
        return False
    first = segments[0]
    first_needs_tts = first.get("type") != "lyric" or include_lyrics
    if first_needs_tts:
        # The first chunk comes from TTS itself and establishes the format for the rest.
        return False
    return any(segment.get("type") != "lyric" or include_lyrics for segment in segments)


def detect_sample_template(segments: list[dict], chunks_dir: Path) -> tuple[int, int, int] | None:
    for index, segment in enumerate(segments):
        if segment.get("type") == "lyric":
            continue
        probe_path = chunks_dir / f"format-probe-{index}.wav"
        try:
            synthesize_wav(segment.get("text", ""), probe_path, segment=segment)
        except HTTPException as exc:
            raise wrap_segment_tts_failure(segment, exc) from exc
        return read_wav_params(probe_path)
    return None


def wrap_segment_tts_failure(segment: dict, exc: HTTPException) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail=build_segment_tts_failure_detail(segment, exc.detail),
    )


def read_wav_params(path: Path) -> tuple[int, int, int] | None:
    if not path.exists():
        return None
    with wave.open(str(path), "rb") as wav:
        return (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())


def build_default_sample_template() -> tuple[int, int, int]:
    return (1, 2, 22050)


def has_lyric_segments(timeline: dict) -> bool:
    return any(segment.get("type") == "lyric" for segment in timeline.get("segments") or [])


def build_segment_tts_failure_detail(segment: dict, original_detail) -> str:
    segment_id = str(segment.get("id") or "unknown")
    segment_number = int(segment.get("index", 0) or 0) + 1
    kind = "歌词" if segment.get("type") == "lyric" else "普通文本"
    preview = " ".join(str(segment.get("text") or "").split())[:80]
    guidance = (
        "这段当前会发送给 AI TTS；如果它其实是歌词，请把片段类型改为“歌词”，"
        "上传 MP3 并设置歌曲片段开始/结束；如果它不是歌词，则需要改写该段或切换可接受这类文本的 TTS 服务。"
    ) if kind != "歌词" else "歌词片段不应进入 AI TTS，请检查时间轴是否已保存为歌词类型。"
    return (
        f"TTS 合成失败：第 {segment_number} 段 ({segment_id})，类型：{kind}。"
        f"片段预览：{preview or '空内容'}。{guidance}原始错误：{original_detail}"
    )


def resolve_lyric_segment_song_paths(project: Project, timeline: dict) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    missing_segments = []
    for segment in timeline.get("segments") or []:
        if segment.get("type") != "lyric":
            continue
        segment_id = str(segment.get("id") or "")
        chapter_id = str(segment.get("chapterId") or "")
        chapter_song = project.chapter_song_path(chapter_id) if chapter_id else None
        if chapter_song and chapter_song.exists():
            paths[segment_id] = chapter_song
            continue
        if project.song_path.exists():
            paths[segment_id] = project.song_path
            continue
        missing_segments.append(segment_id or str(segment.get("index", "?")))

    if missing_segments:
        raise HTTPException(
            status_code=400,
            detail="歌词片段需要先上传章节 MP3 或项目 MP3，再选择歌曲片段开始/结束卡点；后端不会把歌词文本发送给 AI TTS 合成。",
        )
    return paths


def synthesize_wav(text: str, output_path: Path, segment: dict | None = None) -> None:
    """Synthesize one segment through MiniMax ``/v1/t2a_v2``.

    MiniMax is the only supported speech provider and is deliberately not configurable: it is not
    OpenAI-compatible (there is no ``/v1/audio/speech``), so ``TTS_PROVIDER`` no longer exists and
    the whole protocol lives in ``minimax_tts``.

    With ``TTS_RESPONSE_FORMAT=wav`` MiniMax returns 22.05 kHz mono 16-bit PCM, which is exactly
    the format the narration concatenation expects, so the narration path needs no ffmpeg at all.
    ``mp3`` output is converted with ffmpeg.
    """

    api_key = require_tts_api_key()
    model = get_tts_model(minimax_tts.DEFAULT_MODEL)
    voice = get_tts_voice(segment)
    audio_format = minimax_tts.audio_format()
    payload = minimax_tts.build_payload(
        text=text,
        model=model,
        voice=voice,
        segment=segment,
        audio_format_value=audio_format,
    )

    try:
        audio = minimax_tts.request_audio(api_key=api_key, payload=payload, model=model, voice=voice)
    except minimax_tts.MinimaxRequestError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=minimax_tts.failure_detail(
                exc.message,
                base_url_value=minimax_tts.base_url(),
                model=model,
                voice=voice,
                audio_format_value=audio_format,
                attempts=minimax_tts.max_attempts(),
            ),
        ) from exc

    if audio_format == "wav":
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
    else:
        mp3_path = output_path.with_suffix(".mp3")
        mp3_path.parent.mkdir(parents=True, exist_ok=True)
        mp3_path.write_bytes(audio)
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Missing ffmpeg. MiniMax TTS returned MP3, so ffmpeg is required to convert it "
                    "to WAV. Set TTS_RESPONSE_FORMAT=wav to skip the conversion."
                ),
            )
        convert_audio_to_wav(ffmpeg, mp3_path, output_path)

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(status_code=502, detail="MiniMax TTS returned an empty audio file.")


def require_tts_api_key() -> str:
    api_key = os.getenv("TTS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Missing required TTS config: TTS_API_KEY. Set it in .env.")
    return api_key


def get_tts_model(default_model: str) -> str:
    return os.getenv("TTS_MODEL", default_model).strip() or default_model


def get_tts_voice(segment: dict | None = None) -> str:
    default_voice = os.getenv("TTS_VOICE", DEFAULT_TTS_VOICE).strip() or DEFAULT_TTS_VOICE
    if not segment:
        return default_voice

    gender = normalize_segment_value(segment.get("speakerGender"), "unknown").lower()
    speaker_name = normalize_segment_value(segment.get("speakerName"), "旁白")
    pool = get_voice_pool(gender)
    if pool:
        return pool[sum(ord(char) for char in speaker_name) % len(pool)]

    gender_env = {
        "male": "TTS_VOICE_MALE",
        "female": "TTS_VOICE_FEMALE",
        "unknown": "TTS_VOICE_UNKNOWN",
    }.get(gender, "TTS_VOICE_UNKNOWN")
    configured = os.getenv(gender_env)
    return configured.strip() if configured and configured.strip() else default_voice


def get_voice_pool(gender: str) -> list[str]:
    env_name = {
        "male": "TTS_VOICE_POOL_MALE",
        "female": "TTS_VOICE_POOL_FEMALE",
        "unknown": "TTS_VOICE_POOL_UNKNOWN",
    }.get(gender, "TTS_VOICE_POOL_UNKNOWN")
    configured = os.getenv(env_name, "")
    return [item.strip() for item in configured.split(",") if item.strip()]


def normalize_segment_value(value, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


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


def mix_with_song(
    ffmpeg: str,
    narration_path: Path,
    song_path: Path,
    output_path: Path,
    timeline: dict,
    lyric_song_paths: dict[str, Path] | None = None,
) -> None:
    duration = get_wav_duration(narration_path)
    bgm_volume = float(timeline.get("bgmVolume", 0.22) or 0.22)
    narration_filter, narration_label = build_narration_stage(usable_narration_volume(timeline))
    lyric_clips = build_lyric_clip_specs(timeline, song_path, lyric_song_paths or {})
    if lyric_clips:
        song_paths = unique_clip_song_paths(lyric_clips)
        song_input_indexes = {str(path): index + 1 for index, path in enumerate(song_paths)}
        for clip in lyric_clips:
            clip["songInputIndex"] = song_input_indexes[str(clip["songPath"])]
        filter_graph = build_lyric_clip_filter_graph(
            lyric_clips,
            bgm_volume,
            narration_filter=narration_filter,
            narration_label=narration_label,
        )
        song_input_options = []
        song_inputs = [str(path) for path in song_paths]
    else:
        song_start = float(timeline.get("songStartSec", 0.0) or 0.0)
        bgm_source, song_input_options = build_bgm_source(ffmpeg, song_path, song_start, duration, output_path.parent)
        filter_graph = (
            f"{narration_filter}"
            f"[1:a]volume={bgm_volume},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[bgm];"
            f"{narration_label}[bgm]amix=inputs=2:duration=first:dropout_transition=2[out]"
        )
        song_inputs = [str(bgm_source if bgm_source is not None else song_path)]

    command = [
        ffmpeg,
        "-y",
        "-i",
        str(narration_path),
        *song_input_options,
        *sum((["-i", item] for item in song_inputs), []),
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


def build_bgm_source(
    ffmpeg: str,
    song_path: Path,
    song_start: float,
    duration: float,
    work_dir: Path,
) -> tuple[Path | None, list[str]]:
    """Cut the song at the configured start offset and loop that cut to cover the narration.

    ffmpeg's input-level ``-stream_loop`` combined with ``-ss`` only seeks the first pass and
    restarts every later pass at 0s, which drops the user's song start. Decoding the cut once
    and looping it with the ``aloop`` filter keeps the offset for every repetition.
    """
    song_start = max(0.0, song_start)
    if song_start <= 0:
        return None, ["-stream_loop", "-1", "-ss", f"{song_start:.3f}"]

    song_duration = probe_audio_duration(ffmpeg, song_path)
    remaining = song_duration - song_start
    if song_duration <= 0 or remaining <= 0:
        # Unknown length or the start offset is past the end of the song: keep the old behaviour.
        return None, ["-stream_loop", "-1", "-ss", f"{song_start:.3f}"]
    if remaining >= duration:
        return None, ["-ss", f"{song_start:.3f}"]

    sample_rate = 22050
    loop_size = int(round(remaining * sample_rate))
    loop_count = max(1, math.ceil(duration / remaining))
    work_dir.mkdir(parents=True, exist_ok=True)
    bgm_path = work_dir / "bgm-loop.wav"
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{song_start:.3f}",
        "-i",
        str(song_path),
        "-filter_complex",
        f"[0:a]asetpts=PTS-STARTPTS,aloop=loop={loop_count}:size={loop_size},"
        f"atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[bgm]",
        "-map",
        "[bgm]",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(bgm_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not bgm_path.exists() or bgm_path.stat().st_size == 0:
        return None, ["-stream_loop", "-1", "-ss", f"{song_start:.3f}"]
    return bgm_path, []


def probe_audio_duration(ffmpeg: str, path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        candidate = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
        ffprobe = str(candidate) if candidate.is_file() else None
    if not ffprobe:
        return 0.0

    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip()) if result.returncode == 0 else 0.0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def build_lyric_clip_specs(timeline: dict, default_song_path: Path, lyric_song_paths: dict[str, Path]) -> list[dict]:
    specs = []
    for segment in timeline.get("segments") or []:
        if segment.get("type") != "lyric":
            continue

        segment_id = str(segment.get("id") or "")
        start_sec = max(0.0, float(segment.get("startSec", 0.0) or 0.0))
        duration_sec = max(0.1, float(segment.get("durationSec", 0.1) or 0.1))
        clip_start_sec = max(0.0, float(segment.get("songClipStartSec", 0.0) or 0.0))
        clip_end_sec = float(segment.get("songClipEndSec", 0.0) or 0.0)
        clip_duration_sec = clip_end_sec - clip_start_sec if clip_end_sec > clip_start_sec else duration_sec
        specs.append(
            {
                "timelineStartSec": start_sec,
                "clipStartSec": clip_start_sec,
                "durationSec": min(duration_sec, max(0.1, clip_duration_sec)),
                "songPath": lyric_song_paths.get(segment_id, default_song_path),
            }
        )
    return specs


def unique_clip_song_paths(clips: list[dict]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for clip in clips:
        path = clip["songPath"]
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def usable_narration_volume(timeline: dict) -> float:
    """Narration gain for the mix.

    narrationVolume was persisted, clamped and returned by the API but never reached ffmpeg, so
    every value except the 1.0 default was silently ignored. Values are already validated at the
    HTTP boundary; this only guards values read back from an older timeline.json.
    """
    try:
        volume = float(timeline.get("narrationVolume", 1.0))
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(volume) or volume < 0:
        return 1.0
    return volume


def build_narration_stage(volume: float) -> tuple[str, str]:
    """Return the narration filter stage and the label to mix.

    The default gain keeps the original graph untouched so existing projects mix bit-for-bit the
    same as before narrationVolume was wired up.
    """
    if abs(volume - 1.0) < 1e-9:
        return "", "[0:a]"
    return f"[0:a]volume={volume:g}[narration];", "[narration]"


def build_lyric_clip_filter_graph(
    clips: list[dict],
    bgm_volume: float,
    narration_filter: str = "",
    narration_label: str = "[0:a]",
) -> str:
    filters = []
    if narration_filter:
        filters.append(narration_filter.rstrip(";"))
    clip_inputs = []
    source_labels: dict[int, str] = {}
    clips_by_input: dict[int, list[int]] = {}
    for index, clip in enumerate(clips):
        clips_by_input.setdefault(int(clip.get("songInputIndex", 1)), []).append(index)

    for input_index, clip_indexes in clips_by_input.items():
        if len(clip_indexes) == 1:
            source_labels[clip_indexes[0]] = f"[{input_index}:a]"
            continue
        split_outputs = "".join(f"[song{clip_index}]" for clip_index in clip_indexes)
        filters.append(f"[{input_index}:a]asplit={len(clip_indexes)}{split_outputs}")
        for clip_index in clip_indexes:
            source_labels[clip_index] = f"[song{clip_index}]"

    for index, clip in enumerate(clips):
        source_label = source_labels[index]
        output_label = f"[clip{index}]"
        delay_ms = int(round(clip["timelineStartSec"] * 1000))
        filters.append(
            f"{source_label}atrim=start={clip['clipStartSec']:.3f}:duration={clip['durationSec']:.3f},"
            f"asetpts=PTS-STARTPTS,volume={bgm_volume},adelay={delay_ms}:all=1{output_label}"
        )
        clip_inputs.append(output_label)

    filters.append(
        f"{narration_label}{''.join(clip_inputs)}amix=inputs={len(clip_inputs) + 1}:duration=first:dropout_transition=0[out]"
    )
    return ";".join(filters)


def get_wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())
