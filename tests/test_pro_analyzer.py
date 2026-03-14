import pytest
import requests
from unittest.mock import patch, MagicMock
from hardware import SystemInfo

import pro_analyzer
from models import MailMeta
from config import OllamaConfig, DEFAULT_SYSTEM_PROMPT


def test_check_ollama_health_success():
    with patch("pro_analyzer._get_session") as mock_session_fn:
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "qwen3.5:2B"}]}
        mock_session.get.return_value = mock_resp
        
        ok, msg = pro_analyzer.check_ollama_health(model="qwen3.5:2B")
        assert ok is True
        assert "mevcut" in msg.lower() or "çalışıyor" in msg.lower()


def test_check_ollama_health_connection_error():
    with patch("pro_analyzer._get_session") as mock_session_fn:
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        
        mock_session.get.side_effect = requests.ConnectionError("Connection Refused")
        
        ok, msg = pro_analyzer.check_ollama_health()
        assert ok is False
        assert "bağlanılamıyor" in msg.lower()


def test_ollama_provider_analyze():
    cfg = OllamaConfig(model="test_model", base_url="http://test")
    provider = pro_analyzer.OllamaProvider(cfg)
    
    meta = MailMeta(uid="1", subject="Buy now", sender="test@test.com", body_preview="Sale sale sale")
    
    with patch("pro_analyzer._get_session") as mock_session_fn:
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session

        mock_resp = MagicMock()
        # Make the LLM respond with SIL and a reason
        mock_resp.json.return_value = {"message": {"content": "{\"decision\":\"SIL\",\"reason\":\"promosyon\"}"}}
        mock_session.post.return_value = mock_resp
        
        result = provider.analyze(meta)
        
        mock_session.post.assert_called_once()
        assert result.decision == "SIL"
        assert "llm:sil" in result.reason.lower()


def test_provider_cache():
    # Make sure _provider_cache is empty initially for this test
    pro_analyzer._provider_cache.clear()
    
    cfg = OllamaConfig(model="foo", base_url="http://bar")
    meta = MailMeta(uid="1")
    
    with patch.object(pro_analyzer.OllamaProvider, "analyze") as mock_analyze:
        mock_analyze.return_value = "mock_result"
        
        # First call should populate cache
        res1 = pro_analyzer.pro_analyze(meta, cfg)
        
        # Second call should use cache
        res2 = pro_analyzer.pro_analyze(meta, cfg)
        
        assert len(pro_analyzer._provider_cache) == 1
        assert res1 == "mock_result"
        assert res2 == "mock_result"
        assert mock_analyze.call_count == 2  # Method itself is called twice on the *same* instance


def test_parse_llm_response_handles_turkish_dotted_i():
    cfg = OllamaConfig(model="test_model", base_url="http://test")
    provider = pro_analyzer.OllamaProvider(cfg)

    decision, reason = provider._parse_llm_response("Karar: SİL")

    assert decision == "SIL"
    assert isinstance(reason, str)


def test_parse_llm_response_handles_json_decision():
    cfg = OllamaConfig(model="test_model", base_url="http://test")
    provider = pro_analyzer.OllamaProvider(cfg)

    decision, reason = provider._parse_llm_response('{"decision":"SIL"}')

    assert decision == "SIL"
    assert isinstance(reason, str)


def test_select_ollama_runtime_options_cpu_only(monkeypatch):
    cpu_only = SystemInfo(
        cpu_count=16,
        total_ram_gb=32.0,
        available_ram_gb=20.0,
        has_gpu=False,
        gpu_name="None",
        vram_total_gb=0.0,
        vram_available_gb=0.0,
        gpu_driver="None",
    )
    monkeypatch.setattr(pro_analyzer, "get_system_info", lambda: cpu_only)
    monkeypatch.setattr(pro_analyzer, "detect_model_size", lambda _model: 2.0)

    options = pro_analyzer._select_ollama_runtime_options("qwen3.5:2B")

    assert options["num_gpu"] == 0
    assert options["use_flash_attn"] is False
    assert options["num_thread"] == 12


def test_select_ollama_runtime_options_gpu(monkeypatch):
    gpu_system = SystemInfo(
        cpu_count=12,
        total_ram_gb=32.0,
        available_ram_gb=16.0,
        has_gpu=True,
        gpu_name="NVIDIA RTX 4070",
        vram_total_gb=12.0,
        vram_available_gb=9.0,
        gpu_driver="555.85",
    )
    monkeypatch.setattr(pro_analyzer, "get_system_info", lambda: gpu_system)
    monkeypatch.setattr(pro_analyzer, "detect_model_size", lambda _model: 4.0)

    options = pro_analyzer._select_ollama_runtime_options("qwen3.5:4B")

    assert options["num_gpu"] == 32
    assert options["use_flash_attn"] is True
    assert options["num_thread"] == 8



def test_ollama_provider_uses_dynamic_runtime_options():
    cfg = OllamaConfig(model="test_model", base_url="http://test")
    expected_options = {
        "num_predict": 256,
        "temperature": 0.0,
        "num_thread": 6,
        "num_gpu": 32,
        "use_flash_attn": True,
    }

    with patch("pro_analyzer._select_ollama_runtime_options", return_value=expected_options):
        provider = pro_analyzer.OllamaProvider(cfg)

    meta = MailMeta(uid="1", subject="Buy now", sender="test@test.com", body_preview="Sale sale sale")

    with patch("pro_analyzer._get_session") as mock_session_fn:
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "{\"decision\":\"SIL\"}"}}
        mock_session.post.return_value = mock_resp

        provider.analyze(meta)

        sent_payload = mock_session.post.call_args.kwargs["json"]
        assert sent_payload["options"] == expected_options


def test_pro_analyze_always_routes_to_provider_even_for_known_patterns():
    pro_analyzer._provider_cache.clear()
    cfg = OllamaConfig(model="foo", base_url="http://bar")
    meta = MailMeta(
        uid="1",
        subject="Kickstarter: Projeniz desteklendi - yeni guncelleme",
        sender="no-reply@kickstarter.com",
        body_preview="Retro klavye projesine yeni destekci geldi.",
    )

    with patch.object(pro_analyzer.OllamaProvider, "analyze") as mock_analyze:
        mock_analyze.return_value = "mock_result"
        result = pro_analyzer.pro_analyze(meta, cfg)

    assert result == "mock_result"
    mock_analyze.assert_called_once()


def test_default_prompt_contains_typical_examples():
    expected_phrases = [
        "Sen bir e-posta temizleme asistanısın",
        "SIL (Silinecekler - Gereksiz)",
        "TUT (Tutulacaklar - Önemli)",
        "JSON formatında cevap ver",
    ]

    for phrase in expected_phrases:
        assert phrase in DEFAULT_SYSTEM_PROMPT
