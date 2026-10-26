from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from backend.app.services import analyzer


def analysis_payload(text: str, kind: str = "narration") -> dict:
    return {
        "segments": [
            {
                "type": kind,
                "text": text,
                "confidence": 0.9,
                "reason": "测试替身",
                "speakerName": "旁白",
                "speakerGender": "unknown",
                "emotion": "neutral",
                "voiceStyle": "neutral_narrator",
                "delivery": "自然朗读。",
                "durationSec": 3.0,
            }
        ],
        "songCandidates": [],
    }


def analysis_json(text: str) -> str:
    return json.dumps(analysis_payload(text), ensure_ascii=False)


class FakeStructuredChain:
    """Stand-in for the runnable returned by ``with_structured_output``."""

    def __init__(self, responder) -> None:
        self.responder = responder
        self.calls: list[str] = []

    def invoke(self, payload: dict) -> analyzer.LlmAnalysis:
        self.calls.append(payload["text"])
        raw = self.responder(payload["text"])
        return analyzer.LlmAnalysis.model_validate(json.loads(raw) if isinstance(raw, str) else raw)


class FakeStructuredLLM:
    def __init__(self, responder) -> None:
        self.responder = responder
        self.schemas: list[object] = []
        self.methods: list[str | None] = []
        self.chains: list[FakeStructuredChain] = []

    def with_structured_output(self, schema, method=None, **_kwargs):
        self.schemas.append(schema)
        self.methods.append(method)
        chain = FakeStructuredChain(self.responder)
        self.chains.append(chain)
        return chain


class FakePrompt:
    """Composition helper: ``prompt | runnable`` just hands the runnable through."""

    def __init__(self, responder) -> None:
        self.responder = responder

    def __or__(self, other):
        return other


class FakeLegacyResponse:
    def __init__(self, content) -> None:
        self.content = content


class FakeLegacyLLM:
    """A text-only fake model for the ``ANALYZER_STRUCTURED_OUTPUT=0`` path."""

    def __init__(self, responder) -> None:
        self.responder = responder
        self.calls: list[str] = []

    def invoke(self, payload: dict) -> FakeLegacyResponse:
        self.calls.append(payload["text"])
        return FakeLegacyResponse(self.responder(payload["text"]))


def install_fake_model(monkeypatch, model) -> None:
    monkeypatch.setattr(analyzer, "build_analyzer_llm", lambda: (FakePrompt(None), model))


# --------------------------------------------------------------------------------------
# structured output is the default path
# --------------------------------------------------------------------------------------


def test_analyze_text_uses_structured_output_by_default(monkeypatch):
    monkeypatch.delenv("ANALYZER_STRUCTURED_OUTPUT", raising=False)
    monkeypatch.delenv("ANALYZER_STRUCTURED_METHOD", raising=False)
    model = FakeStructuredLLM(lambda _text: analysis_json("旁白内容。"))
    install_fake_model(monkeypatch, model)

    result = analyzer.analyze_text("第一章 登台\n旁白内容。")

    assert result["analysisEngine"] == "langchain"
    assert [segment["text"] for segment in result["segments"]] == ["旁白内容。"]
    assert model.schemas == [analyzer.LlmAnalysis]
    assert model.methods == ["function_calling"]
    assert len(model.chains) == 1
    assert len(model.chains[0].calls) == 1


def test_analyze_text_merges_one_structured_result_per_chunk(monkeypatch):
    monkeypatch.setenv("ANALYZER_CHUNK_CHARS", "200")
    model = FakeStructuredLLM(lambda text: analysis_json("另一段旁白。" if "另一段旁白" in text else "旁白内容。"))
    install_fake_model(monkeypatch, model)
    text = "第一章 登台\n" + "旁白内容。" * 40 + "\n\n" + "另一段旁白。" * 40

    result = analyzer.analyze_text(text)

    assert len(model.chains[0].calls) == 2
    assert len(result["segments"]) == 2


def test_analyze_text_returns_an_empty_analysis_for_blank_text(monkeypatch):
    model = FakeStructuredLLM(lambda _text: analysis_json("不该被调用。"))
    install_fake_model(monkeypatch, model)

    result = analyzer.analyze_text("   \n  ")

    assert result["segments"] == []
    assert result["songCandidates"] == []
    assert all(chain.calls == [] for chain in model.chains)


def test_analyze_text_wraps_structured_failures_in_a_502(monkeypatch):
    monkeypatch.delenv("ANALYZER_STRUCTURED_OUTPUT", raising=False)
    model = FakeStructuredLLM(lambda _text: "这不是 JSON。")
    install_fake_model(monkeypatch, model)

    with pytest.raises(HTTPException) as exc_info:
        analyzer.analyze_text("第一章 登台\n旁白内容。")

    assert exc_info.value.status_code == 502
    assert "LangChain analyzer failed" in str(exc_info.value.detail)


def test_analyze_text_still_requires_an_api_key_in_structured_mode(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANALYZER_STRUCTURED_OUTPUT", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        analyzer.analyze_text("测试文本")

    assert exc_info.value.status_code == 503
    assert "OPENAI_API_KEY" in str(exc_info.value.detail)


# --------------------------------------------------------------------------------------
# env switches
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("off", False),
        ("FALSE", False),
        ("  off  ", False),
    ],
)
def test_structured_output_switch_reads_the_env_flag(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("ANALYZER_STRUCTURED_OUTPUT", raising=False)
    else:
        monkeypatch.setenv("ANALYZER_STRUCTURED_OUTPUT", value)

    assert analyzer.analyzer_structured_output_enabled() is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "function_calling"),
        ("function_calling", "function_calling"),
        ("json_mode", "json_mode"),
        ("json_schema", "json_schema"),
        ("JSON_MODE", "json_mode"),
        ("bogus", "function_calling"),
        ("", "function_calling"),
    ],
)
def test_structured_method_env_falls_back_to_the_default(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("ANALYZER_STRUCTURED_METHOD", raising=False)
    else:
        monkeypatch.setenv("ANALYZER_STRUCTURED_METHOD", value)

    assert analyzer.analyzer_structured_output_method() == expected


def test_build_structured_analyzer_llm_passes_the_configured_method(monkeypatch):
    monkeypatch.setenv("ANALYZER_STRUCTURED_METHOD", "json_mode")
    model = FakeStructuredLLM(lambda _text: analysis_json("旁白内容。"))
    install_fake_model(monkeypatch, model)

    prompt, structured_llm = analyzer.build_structured_analyzer_llm()

    assert isinstance(prompt, FakePrompt)
    assert model.schemas == [analyzer.LlmAnalysis]
    assert model.methods == ["json_mode"]
    assert isinstance(structured_llm, FakeStructuredChain)


def test_build_structured_analyzer_llm_retries_without_method_on_type_error(monkeypatch):
    class OldLangChainLLM:
        """Older langchain-openai releases reject the ``method`` keyword."""

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def with_structured_output(self, schema, **kwargs):
            self.calls.append({"schema": schema, **kwargs})
            if "method" in kwargs:
                raise TypeError("with_structured_output() got an unexpected keyword argument 'method'")
            return FakeStructuredChain(lambda _text: analysis_json("旁白内容。"))

    old_model = OldLangChainLLM()
    monkeypatch.delenv("ANALYZER_STRUCTURED_METHOD", raising=False)
    install_fake_model(monkeypatch, old_model)

    _prompt, structured_llm = analyzer.build_structured_analyzer_llm()

    assert [call.get("method") for call in old_model.calls] == ["function_calling", None]
    assert structured_llm.invoke({"text": "旁白内容。"}).segments[0].text == "旁白内容。"


def test_real_langchain_wiring_accepts_the_llm_analysis_schema(monkeypatch):
    """The real ChatOpenAI path must be able to convert ``LlmAnalysis`` for function calling.

    This never calls the network: it only proves langchain-openai can build the structured
    runnable from our schema, which is where a schema change would break first.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("ANALYZER_STRUCTURED_METHOD", "function_calling")

    prompt, structured_llm = analyzer.build_structured_analyzer_llm()

    assert hasattr(prompt, "invoke")
    assert hasattr(structured_llm, "invoke")


# --------------------------------------------------------------------------------------
# legacy text + parse path stays available
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("disabled_value", ["0", "false", "off"])
def test_analyze_text_falls_back_to_the_legacy_parser_when_disabled(monkeypatch, disabled_value):
    monkeypatch.setenv("ANALYZER_STRUCTURED_OUTPUT", disabled_value)
    legacy_model = FakeLegacyLLM(lambda _text: analysis_json("旁白内容。"))
    install_fake_model(monkeypatch, legacy_model)

    def fail_structured_build():
        raise AssertionError("structured output must not be used when ANALYZER_STRUCTURED_OUTPUT disables it")

    monkeypatch.setattr(analyzer, "build_structured_analyzer_llm", fail_structured_build)

    result = analyzer.analyze_text("第一章 登台\n旁白内容。")

    assert result["analysisEngine"] == "langchain"
    assert [segment["text"] for segment in result["segments"]] == ["旁白内容。"]
    assert len(legacy_model.calls) == 1


def test_legacy_path_wraps_invalid_json_in_a_502(monkeypatch):
    monkeypatch.setenv("ANALYZER_STRUCTURED_OUTPUT", "0")
    legacy_model = FakeLegacyLLM(lambda _text: "这不是 JSON，只是随便一段话。")
    install_fake_model(monkeypatch, legacy_model)

    with pytest.raises(HTTPException) as exc_info:
        analyzer.analyze_text("第一章 登台\n旁白内容。")

    assert exc_info.value.status_code == 502
    assert "LangChain analyzer failed" in str(exc_info.value.detail)


# --------------------------------------------------------------------------------------
# result coercion
# --------------------------------------------------------------------------------------


def test_coerce_llm_analysis_accepts_models_and_dicts():
    model_result = analyzer.LlmAnalysis.model_validate(analysis_payload("旁白内容。"))
    assert analyzer.coerce_llm_analysis(model_result) is model_result

    from_dict = analyzer.coerce_llm_analysis(analysis_payload("旁白内容。"))
    assert from_dict.segments[0].text == "旁白内容。"


def test_coerce_llm_analysis_rejects_unexpected_shapes():
    with pytest.raises(ValueError, match="意外的结果类型"):
        analyzer.coerce_llm_analysis("纯文本")

    with pytest.raises(ValueError, match="不符合 LlmAnalysis"):
        analyzer.coerce_llm_analysis({"segments": [{"text": "缺少 type"}]})
