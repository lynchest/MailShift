import pytest
from mailshift.main import clean_text, format_duration


# ---------------------------------------------------------------------------
# clean_text helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "input_text, expected",
    [
        # Empty/None
        (None, "(bilinmiyor)"),
        ("", "(bilinmiyor)"),
        ("   ", "(bilinmiyor)"),

        # Normal strings
        ("Hello World", "Hello World"),
        ("Normal text here", "Normal text here"),

        # Strings with control characters
        ("Hello\x00World", "Hello World"),
        ("Hello\nWorld", "Hello World"),
        ("Text\twith\ttabs", "Text with tabs"),
        ("Newline\r\nand\rreturn", "Newline and return"),

        # Strings with Turkish characters/accents
        # NOTE: unicodedata.normalize("NFKC", text) does not remove accents, it normalizes characters.
        ("İstanbul", "İstanbul"),
        ("ÇĞIÖŞÜ çğıöşü", "ÇĞIÖŞÜ çğıöşü"),
        ("Café", "Café"),

        # Extra whitespace
        ("  Too   much   spaces  ", "Too much spaces"),

        # Long strings
        ("This is a very long string that should be truncated", "This is a very long string that sho…"),
        ("This is exactly 35 chars long text!", "This is exactly 35 chars long text!"),

        # Control chars that leave nothing
        ("\x00\x01\n\r", "(bilinmiyor)"),
    ],
)
def test_clean_text(input_text, expected):
    """Test clean_text behavior."""
    assert clean_text(input_text, max_len=35) == expected


def test_clean_text_custom_max_len():
    """Test clean_text with a custom max_len."""
    assert clean_text("Hello World", max_len=5) == "Hello…"
    assert clean_text("Hello World", max_len=20) == "Hello World"


# ---------------------------------------------------------------------------
# format_duration helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "seconds, expected",
    [
        # Zero and negatives
        (0, "~0 sn"),
        (0.0, "~0 sn"),
        (-5.5, "~0 sn"),

        # Seconds only (< 60)
        (5, "~5 sn"),
        (45.4, "~45 sn"),
        (45.5, "~46 sn"),
        (59.9, "~1 dk 00 sn"),

        # Exact minutes
        (60, "~1 dk 00 sn"),
        (120, "~2 dk 00 sn"),

        # Minutes and seconds
        (65, "~1 dk 05 sn"),
        (125, "~2 dk 05 sn"),
        (61.2, "~1 dk 01 sn"),
        (3599, "~59 dk 59 sn"),

        # Hours+ (function just returns total minutes)
        (3600, "~60 dk 00 sn"),
        (3665, "~61 dk 05 sn"),
    ],
)
def test_format_duration(seconds, expected):
    """Test format_duration behavior."""
    assert format_duration(seconds) == expected
