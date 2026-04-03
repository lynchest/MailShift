
import sys
import os

# Add src and current dir (for mock requests) to path
sys.path.append(os.path.abspath("src"))
sys.path.append(os.path.abspath("."))

# Mock missing libraries
from unittest.mock import MagicMock

class MockModel:
    pass

pydantic_mock = MagicMock()
pydantic_mock.BaseModel = MockModel
sys.modules["pydantic"] = pydantic_mock
sys.modules["psutil"] = MagicMock()

# Mock config to avoid ForwardRef issues with MagicMock in Union
config_mock = MagicMock()
config_mock.OllamaConfig = MockModel
config_mock.LMStudioConfig = MockModel
sys.modules["mailshift.config.config"] = config_mock

# Mock models
models_mock = MagicMock()
models_mock.MailMeta = MockModel
models_mock.ScanResult = MockModel
sys.modules["mailshift.models.models"] = models_mock

import re
import json
import unicodedata
from typing import Optional, Union, Any

# Mock the logger
import mailshift.utils.logger as logger
logger.log = MagicMock()

# Import the class we want to test
from mailshift.core.analyzers.pro import OllamaProvider

def test_parse_llm_response_with_thinking():
    class MockConfig:
        def __init__(self):
            self.model = "test"
            self.use_think = True

    provider = OllamaProvider(MockConfig())

    # Test Case 1: JSON response with thinking
    response = "<think>This email is about a sale. \n It should be deleted.</think>{\"decision\": \"SIL\", \"reason\": \"promosyon\"}"
    decision, reason = provider._parse_llm_response(response)
    print(f"Test 1 - Decision: {decision}, Reason: {reason}")
    assert decision == "SIL"
    assert "This email is about a sale. It should be deleted." in reason
    assert reason.startswith("(")

    # Test Case 2: Plain text response with thinking
    response = "<think>Personal mail from a friend.</think>TUT because it is personal"
    decision, reason = provider._parse_llm_response(response)
    print(f"Test 2 - Decision: {decision}, Reason: {reason}")
    assert decision == "TUT"
    assert "(Personal mail from a friend.)" in reason
    assert "it is personal" in reason

    # Test Case 3: Long thinking truncation
    long_think = "A" * 200
    response = f"<think>{long_think}</think>{{\"decision\": \"SIL\", \"reason\": \"spam\"}}"
    decision, reason = provider._parse_llm_response(response)
    print(f"Test 3 - Reason length: {len(reason)}")
    # Truncated to 100 chars (97 + ...)
    expected_prefix = "(" + "A" * 97 + "...)"
    assert reason.startswith(expected_prefix)

    # Test Case 4: No thinking
    response = "{\"decision\": \"TUT\", \"reason\": \"important\"}"
    decision, reason = provider._parse_llm_response(response)
    print(f"Test 4 - Decision: {decision}, Reason: {reason}")
    assert decision == "TUT"
    assert reason == "important"

    print("All tests passed!")

if __name__ == "__main__":
    try:
        test_parse_llm_response_with_thinking()
    except AssertionError as e:
        print(f"Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
