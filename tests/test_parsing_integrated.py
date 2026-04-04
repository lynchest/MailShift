from mailshift.config.config import OllamaConfig
from mailshift.core.analyzers.pro import OllamaProvider


def _provider(use_think: bool = True) -> OllamaProvider:
    cfg = OllamaConfig(model="qwen3.5:0.8B", base_url="http://localhost:11434", use_think=use_think)
    return OllamaProvider(cfg)


def test_parse_llm_response_with_thinking() -> None:
    provider = _provider(use_think=True)

    response = "<think>This email is about a sale. \n It should be deleted.</think>{\"decision\": \"SIL\", \"reason\": \"promosyon\"}"
    decision, reason = provider._parse_llm_response(response)
    assert decision == "SIL"
    assert "(This email is about a sale. It should be deleted.)" in reason
    assert "promosyon" in reason

    response = "<think>Personal mail from a friend.</think>TUT because it is personal"
    decision, reason = provider._parse_llm_response(response)
    assert decision == "TUT"
    assert "(Personal mail from a friend.)" in reason
    assert "it is personal" in reason

    long_think = "A" * 200
    response = f"<think>{long_think}</think>{{\"decision\": \"SIL\", \"reason\": \"spam\"}}"
    decision, reason = provider._parse_llm_response(response)
    assert decision == "SIL"
    assert reason.startswith("(" + ("A" * 97) + "...)")

    response = '{"decision": "TUT", "reason": "important"}'
    decision, reason = provider._parse_llm_response(response)
    assert decision == "TUT"
    assert reason == "important"
