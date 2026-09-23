"""Unified Skill framework — bridges V1 BaseTool into a BaseSkill protocol.

This package provides the foundation for the two-mode architecture:
- **Builtin tools** stay in ``python/tools/`` as ``BaseTool`` subclasses,
  wrapped by ``BuiltinToolAdapter`` for transparent integration.
- **Agent Skills** (SKILL.md format) follow the open standard from
  https://agenticskills.io/, wrapped by ``AgentSkillAdapter`` for
  task-venv execution with on-demand dependency installation.

Both modes are unified through ``SkillRegistry``, which the agent
interacts with exclusively.
"""

from skills.context import SkillContext
from skills.protocol import BaseSkill
from skills.loader import BuiltinToolAdapter, AgentSkillAdapter
from skills.registry import SkillRegistry

__all__ = [
    "SkillContext",
    "BaseSkill",
    "BuiltinToolAdapter",
    "AgentSkillAdapter",
    "SkillRegistry",
]
