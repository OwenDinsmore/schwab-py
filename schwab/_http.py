'''
Resolves the HTTP library the same way authlib does, so that the responses and
exceptions produced by authlib's OAuth sessions are the same types schwab-py
checks against. authlib prefers ``httpx2`` when it is installed and falls back
to ``httpx`` otherwise.
'''

from __future__ import annotations

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

__all__ = ['httpx']
