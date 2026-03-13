"""
analyzer.py – Email analysis modules (re-exports for backwards compatibility).
"""

from fast_analyzer import fast_analyze
from pro_analyzer import pro_analyze

__all__ = ["fast_analyze", "pro_analyze"]
