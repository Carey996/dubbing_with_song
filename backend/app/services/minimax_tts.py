"""MiniMax synchronous text-to-speech (``POST /v1/t2a_v2``).

MiniMax is not OpenAI-compatible for speech: there is no ``/v1/audio/speech`` endpoint, the
response is JSON carrying hex-encoded (or URL) audio rather than raw bytes, and there is no
free-text ``instructions`` field — delivery is steered with ``voice_setting.emotion`` instead.
This module keeps that protocol in one place so ``renderer`` can treat it as just another
provider.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Any, NamedTuple

DEFAULT_BASE_URL = "https://api.minimax.cn/v1"
DEFAULT_MODEL = "speech-2.8-hd"
SUPPORTED_FORMATS = ("wav", "mp3")

# The narration track is concatenated with the ``wave`` module, so the WAV branch asks MiniMax
# for exactly the format ``renderer.build_default_sample_template`` produces: this makes the
# whole narration path ffmpeg-free.
WAV_SAMPLE_RATE = 22050
DEFAULT_MP3_SAMPLE_RATE = 32000
DEFAULT_MP3_BITRATE = 128000

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BASE_DELAY = 1.0
MAX_RETRY_BASE_DELAY = 30.0
MIN_SPEED = 0.5
MAX_SPEED = 2.0

# ``base_resp.status_code`` values MiniMax returns for throttling; retrying them is worthwhile.
RETRYABLE_STATUS_CODES = frozenset({1002, 1039})
RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})

# The analyzer emits these emotion labels; MiniMax accepts a fixed enum instead.
EMOTION_MAP = {
    "happy": "happy",
    "sad": "sad",
    "angry": "angry",
    "fearful": "fearful",
    "disgusted": "disgusted",
    "surprised": "surprised",
    "calm": "calm",
    "gentle": "calm",
    "excited": "happy",
    "whisper": "whisper",
    "fluent": "fluent",
}
# speech-2.8 dropped ``whisper``; sending it is a request error rather than a fallback.
WHISPER_UNSUPPORTED_PREFIXES = ("speech-2.8",)

STATUS_MESSAGES = {
    1000: "MiniMax 未知错误",
    1001: "MiniMax 请求超时",
    1002: "MiniMax 触发限流（rate limit）",
    1004: "MiniMax 鉴权失败，请检查 TTS_API_KEY",
    1039: "MiniMax 触发 TPM 限流",
    1042: "文本中无效字符超过 10%",
    2013: "MiniMax 参数不合法",
}


class MinimaxAttempt(NamedTuple):
    """Outcome of one attempt: audio bytes, or the error and whether a retry could help."""

    audio: bytes | None
    error: str
    status_code: int
    retryable: bool


def base_url() -> str:
    configured = (os.getenv("TTS_BASE_URL") or "").strip()
    return (configured or DEFAULT_BASE_URL).rstrip("/")


def audio_format() -> str:
    configured = (os.getenv("TTS_RESPONSE_FORMAT") or "").strip().lower()
    return configured if configured in SUPPORTED_FORMATS else "wav"


def timeout_seconds() -> float:
    return _positive_float("TTS_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)


def max_attempts() -> int:
    return _positive_int("TTS_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)


def retry_delay(attempt: int) -> float:
    """Exponential backoff, capped so a throttled render still finishes in reasonable time."""

    base = _positive_float("TTS_RETRY_BASE_DELAY", DEFAULT_RETRY_BASE_DELAY)
    return min(base * (2 ** max(0, attempt - 1)), MAX_RETRY_BASE_DELAY)


def tts_speed() -> float:
    raw = os.getenv("TTS_SPEED", "1.0")
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(speed):
        return 1.0
    return min(max(speed, MIN_SPEED), MAX_SPEED)


def emotion_for(segment: dict | None, model: str) -> str | None:
    """Map the analyzer's emotion label onto MiniMax's enum, dropping what the model rejects."""

    if not segment:
        return None
    label = str(segment.get("emotion") or "").strip().lower()
    emotion = EMOTION_MAP.get(label)
    if emotion is None:
        return None
    normalized_model = (model or "").lower()
    if emotion == "whisper" and any(normalized_model.startswith(prefix) for prefix in WHISPER_UNSUPPORTED_PREFIXES):
        return None
    return emotion


def audio_settings(audio_format_value: str) -> dict:
    if audio_format_value == "wav":
        return {"sample_rate": WAV_SAMPLE_RATE, "format": "wav", "channel": 1}
    return {
        "sample_rate": DEFAULT_MP3_SAMPLE_RATE,
        "bitrate": DEFAULT_MP3_BITRATE,
        "format": "mp3",
        "channel": 1,
    }


def build_payload(
    *,
    text: str,
    model: str,
    voice: str,
    segment: dict | None,
    audio_format_value: str | None = None,
) -> dict:
    chosen_format = audio_format_value or audio_format()
    voice_setting = {"voice_id": voice, "speed": tts_speed(), "vol": 1, "pitch": 0}
    emotion = emotion_for(segment, model)
    if emotion:
        voice_setting["emotion"] = emotion

    payload: dict[str, Any] = {
        "model": model,
        "text": text or " ",
        "stream": False,
        "language_boost": language_boost(),
        "output_format": "hex",
        "voice_setting": voice_setting,
        "audio_setting": audio_settings(chosen_format),
    }
    return payload


def language_boost() -> str:
    configured = (os.getenv("TTS_LANGUAGE_BOOST") or "").strip()
    if configured:
        return configured
    # Only force Chinese when the caller did not choose, and let MiniMax auto-detect otherwise.
    return "auto"


def open_request(request: urllib.request.Request, timeout: float):
    """Single seam for the HTTP call so tests can drive the retry loop without network access."""

    return urllib.request.urlopen(request, timeout=timeout)


def request_audio(
    *,
    api_key: str,
    payload: dict,
    model: str,
    voice: str,
    sleep=time.sleep,
) -> bytes:
    """POST the synthesis request, retrying throttle responses, and return decoded audio bytes.

    Raises ``MinimaxRequestError`` once every attempt is exhausted or the failure is not
    retryable, so the caller can turn it into an ``HTTPException`` with the renderer's wording.
    """

    url = f"{base_url()}/t2a_v2"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    attempts = max_attempts()
    last: MinimaxAttempt | None = None

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        last = attempt_request(request)
        if last.audio is not None:
            return last.audio
        if not last.retryable or attempt == attempts:
            break
        sleep(retry_delay(attempt))

    assert last is not None  # attempts >= 1 always assigns it
    raise MinimaxRequestError(last.error, status_code=last.status_code, retryable=last.retryable)


class MinimaxRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


def attempt_request(request: urllib.request.Request) -> MinimaxAttempt:
    """One HTTP attempt: transport errors, HTTP errors and MiniMax business errors all normalise."""

    try:
        with open_request(request, timeout_seconds()) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = read_http_error(exc)
        retryable = exc.code in RETRYABLE_HTTP_CODES
        return MinimaxAttempt(None, f"HTTP {exc.code}：{detail}", 503 if retryable else 502, retryable)
    except urllib.error.URLError as exc:
        return MinimaxAttempt(None, f"网络错误：{exc.reason}", 502, True)
    except TimeoutError:
        return MinimaxAttempt(None, "请求 MiniMax TTS 超时。", 502, True)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return MinimaxAttempt(None, f"MiniMax 返回了非 JSON 响应：{exc}", 502, False)

    if not isinstance(payload, dict):
        return MinimaxAttempt(None, "MiniMax 返回了意外的响应结构。", 502, False)

    base_resp = payload.get("base_resp") or {}
    status_code = base_resp.get("status_code")
    if status_code not in (0, None):
        message = str(base_resp.get("status_msg") or STATUS_MESSAGES.get(status_code, "未知错误"))
        retryable = status_code in RETRYABLE_STATUS_CODES
        return MinimaxAttempt(
            None,
            f"MiniMax 错误码 {status_code}（{STATUS_MESSAGES.get(status_code, '未知错误')}）：{message}",
            503 if retryable else 502,
            retryable,
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        return MinimaxAttempt(None, "MiniMax 响应缺少 data 字段。", 502, False)

    audio_hex = data.get("audio")
    if not isinstance(audio_hex, str) or not audio_hex.strip():
        return MinimaxAttempt(None, "MiniMax 返回了空音频。", 502, False)

    try:
        audio = bytes.fromhex(audio_hex.strip())
    except ValueError as exc:
        return MinimaxAttempt(None, f"MiniMax 返回的音频不是合法 hex：{exc}", 502, False)

    if not audio:
        return MinimaxAttempt(None, "MiniMax 返回了空音频。", 502, False)
    return MinimaxAttempt(audio, "", 0, False)


def read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - the body is best-effort diagnostics only
        return str(exc.reason or exc)
    return body[:300] or str(exc.reason or exc)


def failure_detail(
    error: str,
    *,
    base_url_value: str,
    model: str,
    voice: str,
    audio_format_value: str,
    attempts: int,
) -> str:
    return (
        f"MiniMax TTS 失败（尝试 {attempts} 次）：{error}。"
        f"当前配置：TTS_BASE_URL={base_url_value}, TTS_MODEL={model}, TTS_VOICE={voice}, "
        f"TTS_RESPONSE_FORMAT={audio_format_value}。"
        "请确认 TTS_API_KEY 是同一个 MiniMax 国内站 key（国际站 api.minimax.io 会返回 1004/2049），"
        "且 TTS_MODEL 是 speech-2.8-hd / speech-2.8-turbo / speech-02-hd 等语音模型，"
        "TTS_VOICE 是 MiniMax 音色 id（例如 female-yujie）。"
    )


def _positive_float(name: str, fallback: float) -> float:
    try:
        value = float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _positive_int(name: str, fallback: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback
