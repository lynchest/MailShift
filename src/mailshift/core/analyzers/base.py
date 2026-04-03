"""
analyzer.py | Email analysis modules (re-exports for backwards compatibility).
"""

from .fast import fast_analyze
from .pro import pro_analyze

__all__ = ["fast_analyze", "pro_analyze"]
