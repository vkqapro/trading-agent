"""Provider aliases; concrete transport remains in :mod:`src.decision.provider`."""

from src.decision.provider import HttpDecisionProvider, build_provider

__all__ = ["HttpDecisionProvider", "build_provider"]
