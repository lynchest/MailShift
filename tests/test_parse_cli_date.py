import pytest
import click
from datetime import date
from mailshift.main import parse_cli_date

@pytest.mark.parametrize(
    "raw_value, expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        # ISO format
        ("2025-01-01", date(2025, 1, 1)),
        ("2024-12-31", date(2024, 12, 31)),
        # IMAP format
        ("01-Jan-2025", date(2025, 1, 1)),
        ("31-Dec-2024", date(2024, 12, 31)),
        ("1-Feb-2025", date(2025, 2, 1)),
        ("01-jan-2025", date(2025, 1, 1)), # Mixed case month
        ("15-AUG-2023", date(2023, 8, 15)), # Upper case month
        # Slash format
        ("01/01/2025", date(2025, 1, 1)),
        ("31/12/2024", date(2024, 12, 31)),
        # Dot format
        ("01.01.2025", date(2025, 1, 1)),
        ("31.12.2024", date(2024, 12, 31)),
    ],
)
def test_parse_cli_date_valid(raw_value, expected):
    """Test parse_cli_date with valid inputs."""
    assert parse_cli_date(raw_value, "test-option") == expected

@pytest.mark.parametrize(
    "raw_value",
    [
        "2025-13-01",      # Invalid month in ISO
        "2025-01-32",      # Invalid day in ISO
        "01-XYZ-2025",     # Invalid month name in IMAP
        "30-Feb-2025",     # Invalid day for month in IMAP
        "32/01/2025",      # Invalid day in slash format
        "01/13/2025",      # Invalid month in slash format
        "not-a-date",      # Completely invalid
        "2025-01",         # Partial ISO
        "01-Jan",          # Partial IMAP
        "2025/01/01",      # Wrong order for slash (it expects DD/MM/YYYY)
    ],
)
def test_parse_cli_date_invalid(raw_value):
    """Test parse_cli_date with invalid inputs raises click.BadParameter."""
    with pytest.raises(click.BadParameter):
        parse_cli_date(raw_value, "test-option")
