"""Skill execution context — runtime information passed to every Skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillContext:
    """Runtime context available to all Skills during execution.

    Attributes:
        workspace_path: The task's working directory.
        venv_python: Path to the task venv's Python interpreter (empty if none).
        task_id: Unique identifier for the current task.
        settings: Current application settings dict.
        cancel_event: asyncio.Event used to signal task cancellation.
    """

    workspace_path: str
    venv_python: str = ""
    task_id: str = ""
    settings: dict = field(default_factory=dict)
    cancel_event: Any = None  # asyncio.Event
