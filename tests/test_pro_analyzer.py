import pytest
import requests
from unittest.mock import patch, MagicMock

import pro_analyzer
from models import MailMeta
from config import OllamaConfig


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
