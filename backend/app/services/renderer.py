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
DEFAULT_TTS_INSTRUCTIONS = (
    "用自然、有情绪的中文有声书旁白风格朗读。根据句意调整语气、停顿和强弱；"
    "对白更口语，叙述更沉稳；紧张处略微加快并压低，温柔处放慢并柔和。"
    "不要读出舞台提示或说明，只朗读正文。"
)
DEFAULT_OPENROUTER_TTS_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_TTS_MODEL = "openai/gpt-4o-mini-tts-2025-12-15"
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
            try:
                synthesize_wav(text, chunk_path, segment=segment)
            except HTTPException as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=build_segment_tts_failure_detail(segment, exc.detail),
                ) from exc
            with wave.open(str(chunk_path), "rb") as wav:
                sample_template = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())

        chunk_paths.append(chunk_path)

    if not chunk_paths:
        create_silence_wav(output_path, 1.0, (1, 2, 22050))
        return

    concatenate_wavs(chunk_paths, output_path)


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
    provider = os.getenv("TTS_PROVIDER", "openrouter").strip().lower()
    if provider == "openrouter":
        synthesize_wav_with_openrouter(text, output_path, segment=segment)
        return
    if provider == "openai":
        synthesize_wav_with_openai(text, output_path, segment=segment)
        return

    raise HTTPException(status_code=503, detail=f"Unsupported TTS_PROVIDER: {provider}. Use openrouter or openai.")


def synthesize_wav_with_openai(text: str, output_path: Path, segment: dict | None = None) -> None:
    synthesize_audio_with_openai_compatible(
        text=text,
        output_path=output_path,
        default_base_url=None,
        default_model=DEFAULT_TTS_MODEL,
        response_format="wav",
        segment=segment,
    )


def synthesize_wav_with_openrouter(text: str, output_path: Path, segment: dict | None = None) -> None:
    mp3_path = output_path.with_suffix(".mp3")
    response_format = os.getenv("TTS_RESPONSE_FORMAT", "mp3").strip().lower()
    if response_format != "mp3":
        raise HTTPException(status_code=503, detail="OpenRouter TTS currently requires TTS_RESPONSE_FORMAT=mp3 so ffmpeg can convert it to WAV.")

    require_tts_api_key()
    model = get_tts_model(DEFAULT_OPENROUTER_TTS_MODEL)
    validate_openrouter_tts_request(text, model)
    synthesize_audio_with_openai_compatible(
        text=text,
        output_path=mp3_path,
        default_base_url=DEFAULT_OPENROUTER_TTS_BASE_URL,
        default_model=model,
        response_format=response_format,
        segment=segment,
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
    segment: dict | None = None,
) -> None:
    api_key = require_tts_api_key()

    client_kwargs = {"api_key": api_key}
    base_url = os.getenv("TTS_BASE_URL") or default_base_url
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)
    model = get_tts_model(default_model)
    voice = get_tts_voice(segment)
    request_kwargs = {
        "model": model,
        "voice": voice,
        "input": build_tts_input(text or " ", model, segment),
        "response_format": response_format,
        "speed": float(os.getenv("TTS_SPEED", "1.0")),
    }
    instructions = build_tts_instructions(segment)
    if instructions:
        request_kwargs["instructions"] = instructions

    try:
        response = client.audio.speech.create(**request_kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        response.write_to_file(output_path)
    except Exception as exc:
        status_code, detail = build_tts_failure_detail(
            exc=exc,
            base_url=base_url,
            model=model,
            voice=voice,
            response_format=response_format,
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(status_code=502, detail="AI TTS returned an empty audio file.")


def require_tts_api_key() -> str:
    api_key = os.getenv("TTS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Missing required TTS config: TTS_API_KEY. Set it in .env.")
    return api_key


def get_tts_model(default_model: str) -> str:
    return os.getenv("TTS_MODEL", default_model).strip() or default_model


def get_tts_instructions() -> str | None:
    configured = os.getenv("TTS_INSTRUCTIONS")
    if configured is None:
        return DEFAULT_TTS_INSTRUCTIONS
    normalized = configured.strip()
    return normalized or None


def build_tts_instructions(segment: dict | None = None) -> str | None:
    base = get_tts_instructions()
    if not segment:
        return base
    if base is None:
        return None

    details = [
        f"当前片段说话人：{normalize_segment_value(segment.get('speakerName'), '旁白')}",
        f"说话人性别：{normalize_segment_value(segment.get('speakerGender'), 'unknown')}",
        f"情绪：{normalize_segment_value(segment.get('emotion'), 'neutral')}",
        f"音色风格：{normalize_segment_value(segment.get('voiceStyle'), 'neutral_narrator')}",
        f"朗读方式：{normalize_segment_value(segment.get('delivery'), '自然清晰，保持中文有声书旁白节奏。')}",
        "保持同一说话人的声音一致；不要读出这些配音提示，只朗读正文。",
    ]
    return "\n".join([base, *details])


def build_tts_input(text: str, model: str, segment: dict | None = None) -> str:
    if not segment or not is_gemini_tts_model(model) or tts_style_explicitly_disabled():
        return text

    tags = get_gemini_style_tags(segment)
    if not tags:
        return text
    return f"{' '.join(tags)} {text}"


def tts_style_explicitly_disabled() -> bool:
    configured = os.getenv("TTS_INSTRUCTIONS")
    return configured is not None and not configured.strip()


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


def is_gemini_tts_model(model: str) -> bool:
    normalized = (model or "").lower()
    return "gemini" in normalized and "tts" in normalized


def get_gemini_style_tags(segment: dict) -> list[str]:
    emotion = normalize_segment_value(segment.get("emotion"), "neutral").lower()
    delivery = normalize_segment_value(segment.get("delivery"), "").lower()
    tags = []
    if emotion in {"angry", "excited", "sad"}:
        tags.append(f"[{emotion}]")
    if emotion in {"tense", "fearful", "gentle"} or "低声" in delivery or "压低" in delivery:
        tags.append("[whispers]")
    return tags[:2]


def normalize_segment_value(value, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def validate_openrouter_tts_request(text: str, model: str) -> None:
    normalized_model = model.lower()
    if normalized_model.startswith("mistralai/voxtral") and contains_cjk(text):
        raise HTTPException(
            status_code=503,
            detail=(
                f"OpenRouter TTS config is not suitable for Chinese narration: TTS_MODEL={model}. "
                "Voxtral TTS is likely to fail for 中文 input; choose a speech model that supports Chinese, "
                "or switch TTS_PROVIDER to another configured TTS service."
            ),
        )


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text or "")


def build_tts_failure_detail(
    exc: Exception,
    base_url: str | None,
    model: str,
    voice: str,
    response_format: str,
) -> tuple[int, str]:
    original = str(exc)
    is_openrouter = bool(base_url and "openrouter.ai" in base_url.lower())
    is_provider_404 = getattr(exc, "status_code", None) == 404 or "Error code: 404" in original
    is_provider_403 = getattr(exc, "status_code", None) == 403 or "Error code: 403" in original
    no_successful_provider = "No successful provider responses" in original

    if is_openrouter and is_provider_404 and no_successful_provider:
        return (
            503,
            (
                "OpenRouter TTS provider returned 404: No successful provider responses. "
                f"Check TTS_MODEL={model}, TTS_VOICE={voice}, TTS_RESPONSE_FORMAT={response_format}, "
                "input language support, and OpenRouter key/credits. "
                f"Original error: {original}"
            ),
        )

    if is_openrouter and is_provider_403:
        return (
            503,
            (
                "OpenRouter TTS provider rejected the speech request due to provider policy/moderation. "
                "If a tiny harmless test sentence is also rejected, this is likely an OpenRouter account, model, "
                "or upstream speech-provider access issue rather than a timeline text issue. "
                f"Check TTS_MODEL={model}, TTS_VOICE={voice}, TTS_RESPONSE_FORMAT={response_format}, "
                "OpenRouter key/credits, and whether the selected provider currently allows audio speech. "
                "If the rejected segment is actually a lyric or song-like segment, upload the MP3 and align that "
                "lyric segment instead of synthesizing it with TTS. "
                f"Original error: {original}"
            ),
        )

    return 502, f"AI TTS failed: {original}"


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
    lyric_clips = build_lyric_clip_specs(timeline, song_path, lyric_song_paths or {})
    if lyric_clips:
        song_paths = unique_clip_song_paths(lyric_clips)
        song_input_indexes = {str(path): index + 1 for index, path in enumerate(song_paths)}
        for clip in lyric_clips:
            clip["songInputIndex"] = song_input_indexes[str(clip["songPath"])]
        filter_graph = build_lyric_clip_filter_graph(lyric_clips, bgm_volume)
        song_input_options = []
        song_inputs = [str(path) for path in song_paths]
    else:
        song_start = float(timeline.get("songStartSec", 0.0) or 0.0)
        filter_graph = (
            f"[1:a]volume={bgm_volume},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[bgm];"
            "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[out]"
        )
        song_input_options = ["-stream_loop", "-1", "-ss", f"{song_start:.3f}"]
        song_inputs = [str(song_path)]

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


def build_lyric_clip_filter_graph(clips: list[dict], bgm_volume: float) -> str:
    filters = []
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

    filters.append(f"[0:a]{''.join(clip_inputs)}amix=inputs={len(clip_inputs) + 1}:duration=first:dropout_transition=0[out]")
    return ";".join(filters)


def get_wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def estimate_spoken_duration(text: str) -> float:
    return max(1.0, len("".join((text or "").split())) / 6.5)
