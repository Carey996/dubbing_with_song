"""MiniMax TTS provider tests.

MiniMax serves speech over ``POST /v1/t2a_v2`` with hex-encoded audio in a JSON envelope, so
these tests drive the provider through ``minimax_tts.open_request`` and never touch the network.
"""

from __future__ import annotations

import io
import json
import urllib.error
import wave
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.services import minimax_tts, renderer


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> bool:
        return False


def make_wav_bytes(frames: int = 2205, channels: int = 1, sample_width: int = 2, rate: int = 22050) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x02" * frames)
    return buffer.getvalue()


def success_body(audio: bytes | None = None, status_code: int = 0, status_msg: str = "success") -> bytes:
    payload = {
        "data": {"audio": (audio if audio is not None else make_wav_bytes()).hex(), "status": 2},
        "extra_info": {"audio_format": "wav", "audio_sample_rate": 22050, "audio_size": 4},
        "base_resp": {"status_code": status_code, "status_msg": status_msg},
    }
    return json.dumps(payload).encode("utf-8")


def install_transport(monkeypatch, responses: list) -> list:
    """Patch the HTTP seam with a queue of bodies/exception factories; returns the call log."""

    calls: list[dict] = []
    queue = list(responses)

    def fake_open(request, timeout):
        calls.append({"url": request.full_url, "headers": dict(request.headers), "data": request.data, "timeout": timeout})
        assert queue, f"unexpected extra request to {request.full_url} (call #{len(calls)})"
        item = queue.pop(0)
        # urllib.error.HTTPError is both an exception and a file-like object, so it has to be
        # raised explicitly instead of being wrapped in FakeResponse.
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            raise item()
        return FakeResponse(item)

    monkeypatch.setattr(minimax_tts, "open_request", fake_open)
    return calls


def http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.minimax.cn/v1/t2a_v2", code, "err", {}, io.BytesIO(body))


# --------------------------------------------------------------------------------------
# configuration helpers
# --------------------------------------------------------------------------------------


def test_audio_format_defaults_to_wav_and_honours_the_env(monkeypatch):
    monkeypatch.delenv("TTS_RESPONSE_FORMAT", raising=False)
    assert minimax_tts.audio_format() == "wav"

    monkeypatch.setenv("TTS_RESPONSE_FORMAT", "MP3")
    assert minimax_tts.audio_format() == "mp3"

    monkeypatch.setenv("TTS_RESPONSE_FORMAT", "flac")
    assert minimax_tts.audio_format() == "wav", "an unsupported format must not reach the API"


def test_base_url_defaults_to_the_china_endpoint(monkeypatch):
    monkeypatch.delenv("TTS_BASE_URL", raising=False)
    assert minimax_tts.base_url() == "https://api.minimax.cn/v1"

    monkeypatch.setenv("TTS_BASE_URL", "https://api.minimaxi.com/v1/")
    assert minimax_tts.base_url() == "https://api.minimaxi.com/v1", "a trailing slash must not double up"


def test_wav_audio_settings_match_what_the_narration_concatenation_needs():
    assert minimax_tts.audio_settings("wav") == {"sample_rate": 22050, "format": "wav", "channel": 1}
    assert minimax_tts.audio_settings("mp3") == {
        "sample_rate": 32000,
        "bitrate": 128000,
        "format": "mp3",
        "channel": 1,
    }


def test_speed_is_clamped_to_the_minimax_range(monkeypatch):
    monkeypatch.setenv("TTS_SPEED", "3")
    assert minimax_tts.tts_speed() == 2.0

    monkeypatch.setenv("TTS_SPEED", "0.1")
    assert minimax_tts.tts_speed() == 0.5

    monkeypatch.setenv("TTS_SPEED", "不是数字")
    assert minimax_tts.tts_speed() == 1.0

    monkeypatch.setenv("TTS_SPEED", "nan")
    assert minimax_tts.tts_speed() == 1.0


def test_retry_delay_backs_off_exponentially_and_is_capped(monkeypatch):
    monkeypatch.setenv("TTS_RETRY_BASE_DELAY", "1")

    assert minimax_tts.retry_delay(1) == 1
    assert minimax_tts.retry_delay(2) == 2
    assert minimax_tts.retry_delay(3) == 4
    assert minimax_tts.retry_delay(10) == minimax_tts.MAX_RETRY_BASE_DELAY


def test_attempts_default_and_override(monkeypatch):
    monkeypatch.delenv("TTS_MAX_ATTEMPTS", raising=False)
    assert minimax_tts.max_attempts() == 3

    monkeypatch.setenv("TTS_MAX_ATTEMPTS", "5")
    assert minimax_tts.max_attempts() == 5

    monkeypatch.setenv("TTS_MAX_ATTEMPTS", "0")
    assert minimax_tts.max_attempts() == 3, "zero is not a sensible attempt count"


# --------------------------------------------------------------------------------------
# payload
# --------------------------------------------------------------------------------------


def test_build_payload_matches_the_documented_t2a_v2_shape(monkeypatch):
    monkeypatch.setenv("TTS_SPEED", "1.2")
    monkeypatch.setenv("TTS_LANGUAGE_BOOST", "Chinese")

    payload = minimax_tts.build_payload(
        text="他走进房间。",
        model="speech-2.8-hd",
        voice="female-yujie",
        segment={"emotion": "gentle"},
        audio_format_value="wav",
    )

    assert payload["model"] == "speech-2.8-hd"
    assert payload["text"] == "他走进房间。"
    assert payload["stream"] is False
    assert payload["output_format"] == "hex"
    assert payload["language_boost"] == "Chinese"
    assert payload["voice_setting"] == {
        "voice_id": "female-yujie",
        "speed": 1.2,
        "vol": 1,
        "pitch": 0,
        "emotion": "calm",
    }
    assert payload["audio_setting"] == {"sample_rate": 22050, "format": "wav", "channel": 1}


def test_build_payload_omits_emotion_it_cannot_map(monkeypatch):
    monkeypatch.delenv("TTS_LANGUAGE_BOOST", raising=False)

    payload = minimax_tts.build_payload(
        text="旁白。",
        model="speech-2.8-hd",
        voice="male-qn-qingse",
        segment={"emotion": "tense"},
    )

    assert "emotion" not in payload["voice_setting"]
    assert payload["language_boost"] == "auto"


def test_build_payload_never_sends_an_instructions_field():
    # TTS_INSTRUCTIONS and the free-text delivery prompt were removed together with the
    # OpenAI-compatible provider; MiniMax steers delivery through voice_setting.emotion only.
    payload = minimax_tts.build_payload(
        text="旁白。",
        model="speech-2.8-hd",
        voice="female-yujie",
        segment={"emotion": "sad", "delivery": "用低沉的语气朗读。", "voiceStyle": "middle_male_deep"},
    )

    assert "instructions" not in payload, "MiniMax has no free-text instructions field"
    assert payload["voice_setting"]["emotion"] == "sad"
    assert "用低沉的语气朗读。" not in json.dumps(payload, ensure_ascii=False)


def test_build_payload_uses_a_space_for_empty_text():
    payload = minimax_tts.build_payload(text="", model="speech-2.8-hd", voice="female-yujie", segment=None)

    assert payload["text"] == " "


@pytest.mark.parametrize(
    "emotion, expected",
    [
        ("gentle", "calm"),
        ("excited", "happy"),
        ("angry", "angry"),
        ("sad", "sad"),
        ("fearful", "fearful"),
        ("happy", "happy"),
        ("calm", "calm"),
        ("tense", None),
        ("neutral", None),
        ("", None),
    ],
)
def test_emotion_mapping(emotion, expected):
    assert minimax_tts.emotion_for({"emotion": emotion}, "speech-2.8-hd") == expected


def test_emotion_mapping_drops_whisper_on_models_that_reject_it():
    assert minimax_tts.emotion_for({"emotion": "whisper"}, "speech-2.8-hd") is None
    assert minimax_tts.emotion_for({"emotion": "whisper"}, "speech-2.6-hd") == "whisper"


def test_emotion_for_without_a_segment():
    assert minimax_tts.emotion_for(None, "speech-2.8-hd") is None


# --------------------------------------------------------------------------------------
# single attempt
# --------------------------------------------------------------------------------------


def test_attempt_request_decodes_hex_audio(monkeypatch):
    expected = make_wav_bytes()
    install_transport(monkeypatch, [success_body(expected)])

    result = minimax_tts.attempt_request(build_request())

    assert result.audio == expected
    assert result.error == ""
    assert result.retryable is False


def build_request():
    import urllib.request

    return urllib.request.Request(
        "https://api.minimax.cn/v1/t2a_v2",
        data=b"{}",
        method="POST",
        headers={"Authorization": "Bearer test-key", "Content-Type": "application/json"},
    )


def test_attempt_request_reports_a_rate_limit_as_retryable(monkeypatch):
    install_transport(monkeypatch, [success_body(audio=b"", status_code=1039, status_msg="TPM rate limit")])

    result = minimax_tts.attempt_request(build_request())

    assert result.audio is None
    assert result.retryable is True
    assert result.status_code == 503
    assert "1039" in result.error


def test_attempt_request_does_not_retry_a_parameter_error(monkeypatch):
    install_transport(monkeypatch, [success_body(audio=b"", status_code=2013, status_msg="invalid params")])

    result = minimax_tts.attempt_request(build_request())

    assert result.retryable is False
    assert result.status_code == 502
    assert "2013" in result.error


def test_attempt_request_explains_the_site_mismatch_code(monkeypatch):
    # 2049 is what the China endpoint returns for a key issued on the international site.
    install_transport(monkeypatch, [success_body(audio=b"", status_code=2049, status_msg="invalid api key")])

    result = minimax_tts.attempt_request(build_request())

    assert result.retryable is False
    assert "2049" in result.error
    assert "站点不匹配" in result.error


def test_attempt_request_reports_auth_failure_with_the_right_code(monkeypatch):
    install_transport(monkeypatch, [success_body(audio=b"", status_code=1004, status_msg="invalid api key")])

    result = minimax_tts.attempt_request(build_request())

    assert result.retryable is False
    assert "鉴权失败" in result.error


def test_attempt_request_retries_http_429_but_not_http_400(monkeypatch):
    install_transport(monkeypatch, [http_error(429, b"slow down")])
    retryable = minimax_tts.attempt_request(build_request())
    assert retryable.retryable is True and retryable.status_code == 503
    assert "slow down" in retryable.error

    install_transport(monkeypatch, [http_error(400, b"bad request")])
    fatal = minimax_tts.attempt_request(build_request())
    assert fatal.retryable is False and fatal.status_code == 502


def test_attempt_request_retries_transport_errors(monkeypatch):
    install_transport(monkeypatch, [lambda: urllib.error.URLError("connection reset")])

    result = minimax_tts.attempt_request(build_request())

    assert result.retryable is True
    assert "网络错误" in result.error


def test_attempt_request_rejects_malformed_responses(monkeypatch):
    for body, fragment in [
        (b"not json", "非 JSON"),
        (b"[1,2,3]", "意外的响应结构"),
        (b'{"base_resp":{"status_code":0}}', "缺少 data"),
        (b'{"base_resp":{"status_code":0},"data":{}}', "空音频"),
        (b'{"base_resp":{"status_code":0},"data":{"audio":"zzz"}}', "不是合法 hex"),
        (b'{"base_resp":{"status_code":0},"data":{"audio":""}}', "空音频"),
    ]:
        install_transport(monkeypatch, [body])
        result = minimax_tts.attempt_request(build_request())
        assert result.audio is None, body
        assert fragment in result.error, (body, result.error)
        assert result.retryable is False


# --------------------------------------------------------------------------------------
# retry loop
# --------------------------------------------------------------------------------------


def test_request_audio_retries_a_throttled_attempt_then_succeeds(monkeypatch):
    audio = make_wav_bytes()
    calls = install_transport(
        monkeypatch,
        [success_body(audio=b"", status_code=1002, status_msg="rate limit"), success_body(audio)],
    )
    sleeps: list[float] = []

    result = minimax_tts.request_audio(
        api_key="test-key",
        payload={"model": "speech-2.8-hd"},
        model="speech-2.8-hd",
        voice="female-yujie",
        sleep=sleeps.append,
    )

    assert result == audio
    assert len(calls) == 2
    assert len(sleeps) == 1, "only the throttled attempt waits"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert calls[0]["url"] == "https://api.minimax.cn/v1/t2a_v2"


def test_request_audio_gives_up_after_the_attempt_budget(monkeypatch):
    monkeypatch.setenv("TTS_MAX_ATTEMPTS", "3")
    calls = install_transport(monkeypatch, [success_body(audio=b"", status_code=1039)] * 3)
    sleeps: list[float] = []

    with pytest.raises(minimax_tts.MinimaxRequestError) as exc:
        minimax_tts.request_audio(
            api_key="test-key",
            payload={},
            model="speech-2.8-hd",
            voice="female-yujie",
            sleep=sleeps.append,
        )

    assert len(calls) == 3
    assert len(sleeps) == 2, "the final attempt is not followed by a sleep"
    assert exc.value.retryable is True
    assert exc.value.status_code == 503


def test_request_audio_does_not_retry_a_fatal_error(monkeypatch):
    calls = install_transport(monkeypatch, [success_body(audio=b"", status_code=2013)])
    sleeps: list[float] = []

    with pytest.raises(minimax_tts.MinimaxRequestError) as exc:
        minimax_tts.request_audio(api_key="k", payload={}, model="m", voice="v", sleep=sleeps.append)

    assert len(calls) == 1
    assert sleeps == []
    assert exc.value.status_code == 502


def test_failure_detail_lists_the_configuration_and_the_china_domain_hint():
    detail = minimax_tts.failure_detail(
        "MiniMax 错误码 1004（MiniMax 鉴权失败，请检查 TTS_API_KEY）：invalid api key",
        base_url_value="https://api.minimax.io/v1",
        model="speech-2.8-hd",
        voice="female-yujie",
        audio_format_value="wav",
        attempts=3,
    )

    assert "尝试 3 次" in detail
    assert "TTS_BASE_URL=https://api.minimax.io/v1" in detail
    assert "TTS_VOICE=female-yujie" in detail
    assert "api.minimax.io" in detail and "1004" in detail


# --------------------------------------------------------------------------------------
# renderer integration
# --------------------------------------------------------------------------------------


def enable_minimax(monkeypatch, audio_format_value: str = "wav") -> None:
    # MiniMax is the only provider now, so there is no TTS_PROVIDER to set.
    monkeypatch.setenv("TTS_API_KEY", "test-key")
    monkeypatch.setenv("TTS_BASE_URL", "https://api.minimax.cn/v1")
    monkeypatch.setenv("TTS_MODEL", "speech-2.8-hd")
    monkeypatch.setenv("TTS_VOICE", "female-yujie")
    monkeypatch.setenv("TTS_RESPONSE_FORMAT", audio_format_value)


def test_synthesize_wav_writes_a_wav_the_renderer_can_concatenate(monkeypatch, tmp_path):
    enable_minimax(monkeypatch)
    install_transport(monkeypatch, [success_body()])
    output_path = tmp_path / "segment.wav"

    renderer.synthesize_wav("他走进房间。", output_path, segment={"emotion": "gentle", "speakerGender": "female"})

    assert output_path.exists()
    assert renderer.read_wav_params(output_path) == (1, 2, 22050)


def test_build_narration_track_uses_minimax_without_ffmpeg(monkeypatch, tmp_path):
    enable_minimax(monkeypatch)
    install_transport(
        monkeypatch,
        [success_body(make_wav_bytes(frames=2205)), success_body(make_wav_bytes(frames=4410))],
    )
    monkeypatch.setattr(renderer, "find_ffmpeg", lambda: pytest.fail("wav output must not need ffmpeg"))

    timeline = {
        "segments": [
            {"id": "seg-001", "type": "narration", "text": "第一句。", "durationSec": 2.0},
            {"id": "seg-002", "type": "narration", "text": "第二句。", "durationSec": 2.0},
        ]
    }
    output_path = tmp_path / "narration.wav"

    renderer.build_narration_track(timeline, tmp_path / "chunks", output_path, include_lyrics=True)

    assert renderer.read_wav_params(output_path) == (1, 2, 22050)
    with wave.open(str(output_path), "rb") as handle:
        assert handle.getnframes() == 2205 + 4410


def test_minimax_mp3_output_requires_ffmpeg(monkeypatch, tmp_path):
    enable_minimax(monkeypatch, audio_format_value="mp3")
    install_transport(monkeypatch, [success_body(audio=b"ID3fake-mp3")])
    monkeypatch.setattr(renderer, "find_ffmpeg", lambda: None)

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("旁白。", tmp_path / "segment.wav")

    assert exc.value.status_code == 503
    assert "TTS_RESPONSE_FORMAT=wav" in str(exc.value.detail)


def test_minimax_mp3_output_is_converted_when_ffmpeg_exists(monkeypatch, tmp_path):
    enable_minimax(monkeypatch, audio_format_value="mp3")
    install_transport(monkeypatch, [success_body(audio=b"ID3fake-mp3")])
    monkeypatch.setattr(renderer, "find_ffmpeg", lambda: "/fake/ffmpeg")
    converted: list[tuple] = []

    def fake_convert(ffmpeg, input_path, output_path):
        converted.append((ffmpeg, Path(input_path).name, Path(output_path).name))
        output_path.write_bytes(make_wav_bytes())

    monkeypatch.setattr(renderer, "convert_audio_to_wav", fake_convert)

    renderer.synthesize_wav("旁白。", tmp_path / "segment.wav")

    assert converted == [("/fake/ffmpeg", "segment.mp3", "segment.wav")]
    assert (tmp_path / "segment.wav").exists()


def test_synthesize_wav_surfaces_a_minimax_failure_as_an_http_error(monkeypatch, tmp_path):
    enable_minimax(monkeypatch)
    install_transport(monkeypatch, [success_body(audio=b"", status_code=1004, status_msg="invalid api key")])

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("旁白。", tmp_path / "segment.wav")

    assert exc.value.status_code == 502
    assert "MiniMax" in str(exc.value.detail)
    assert "TTS_API_KEY" in str(exc.value.detail)


def test_synthesize_wav_requires_an_api_key(monkeypatch, tmp_path):
    enable_minimax(monkeypatch)
    monkeypatch.delenv("TTS_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        renderer.synthesize_wav("旁白。", tmp_path / "segment.wav")

    assert exc.value.status_code == 503
    assert "TTS_API_KEY" in str(exc.value.detail)


def test_minimax_provider_reuses_the_voice_pool_mapping(monkeypatch, tmp_path):
    enable_minimax(monkeypatch)
    monkeypatch.setenv("TTS_VOICE_POOL_FEMALE", "female-shaonv,female-tianmei")
    calls = install_transport(monkeypatch, [success_body(), success_body()])

    renderer.synthesize_wav("甲。", tmp_path / "a.wav", segment={"speakerGender": "female", "speakerName": "小美"})
    renderer.synthesize_wav("乙。", tmp_path / "b.wav", segment={"speakerGender": "female", "speakerName": "小美"})

    voices = [json.loads(call["data"].decode("utf-8"))["voice_setting"]["voice_id"] for call in calls]
    assert voices[0] == voices[1], "the same speaker must map to a stable voice"
    assert voices[0] in {"female-shaonv", "female-tianmei"}
