"""
models.py – Shared data classes for MailShift.
"""

from dataclasses import dataclass


@dataclass
class MailMeta:
    """Lightweight representation of an email message."""

    uid: str
    subject: str = ""
    sender: str = ""
    date: str = ""
    size_bytes: int = 0
    body_preview: str = ""


@dataclass
class ScanResult:
    """Analysis result for a single message."""

    mail: MailMeta
    decision: str = "TUT"
    reason: str = ""


@dataclass
class ScanStats:
    """Aggregate statistics for a scan session."""

    total_scanned: int = 0
    marked_for_deletion: int = 0
    total_size_bytes: int = 0
    marked_size_bytes: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def space_saved_mb(self) -> float:
        return self.marked_size_bytes / (1024 * 1024)
