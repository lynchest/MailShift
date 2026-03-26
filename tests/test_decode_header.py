
from unittest.mock import patch, MagicMock
import pytest
import sys

# We use a context manager to mock sys.modules during imports within the tests.
# This avoids global side-effects and allows tests to run without all dependencies.

@pytest.fixture(scope="module")
def engine_module():
    """Mock dependencies and import the module under test."""
    with patch.dict("sys.modules", {
        "bs4": MagicMock(),
        "pydantic": MagicMock(),
        "psutil": MagicMock(),
        "requests": MagicMock(),
        "requests.adapters": MagicMock(),
        "rich": MagicMock(),
        "rich.console": MagicMock(),
        "rich.logging": MagicMock(),
        "keyring": MagicMock(),
        "pynvml": MagicMock(),
        "lxml": MagicMock(),
    }):
        import mailshift.core.engine
        yield mailshift.core.engine

@pytest.mark.parametrize("input_val, expected", [
    (None, ""),
    ("", ""),
    ("Simple subject", "Simple subject"),
    (b"Simple subject bytes", "Simple subject bytes"),
    ("=?utf-8?q?Encoded_Subject?=", "Encoded Subject"),
    ("=?iso-8859-1?q?Subject_with_=F6?=", "Subject with ö"),
    ("=?utf-8?q?T=C3=BCrk=C3=A7e_Karakterler?=", "Türkçe Karakterler"),
    # Mixed encoded and plain.
    # email.header.decode_header('Hello =?utf-8?q?World?=') returns [(b'Hello ', None), (b'World', 'utf-8')]
    # " ".join() adds another space, resulting in "Hello  World".
    ("Hello =?utf-8?q?World?=", "Hello  World"),
    # Malformed header
    ("=?utf-8?q?malformed", "=?utf-8?q?malformed"),
    # Bytes with non-utf8 (should use 'replace' due to errors="replace")
    (b"Subject \xff with error", "Subject \ufffd with error"),
])
def test_decode_header_value(engine_module, input_val, expected):
    """Test the _decode_header_value helper with various string, byte, and encoded inputs."""
    assert engine_module._decode_header_value(input_val) == expected
