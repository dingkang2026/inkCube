"""python/llm/ — Shared LLM client for all features.

This module is a shared utility, NOT part of the agent (产业洞察) subsystem.
Both the agent and the knowledge base may import from here.
"""
from .client import LLMClient

__all__ = ["LLMClient"]
