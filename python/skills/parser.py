"""SKILL.md parser — parse Agent Skills standard markdown files.

Supports the open standard defined at https://agenticskills.io/
A SKILL.md file consists of:
  - YAML frontmatter (between --- markers): name, description, compatibility, etc.
  - Markdown body: instructions, steps, examples, etc.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Frontmatter field constraints (per Agent Skills specification)
_MAX_NAME_LEN = 64
_MAX_DESC_LEN = 1024
_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$")


def parse_skill_md(path: str) -> dict[str, Any]:
    """Parse a SKILL.md file into structured data.

    Args:
        path: Path to the SKILL.md file.

    Returns:
        Dict with keys:
          - frontmatter: dict of YAML metadata (name, description, etc.)
          - body: str of markdown content after frontmatter
          - raw: str of the full file content

        Returns empty dict if file doesn't exist or can't be parsed.
    """
    if not os.path.isfile(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning("Failed to read SKILL.md at %s: %s", path, e)
        return {}

    return parse_skill_md_content(content)


def parse_skill_md_content(content: str) -> dict[str, Any]:
    """Parse SKILL.md content string into structured data.

    Args:
        content: Full content of a SKILL.md file.

    Returns:
        Dict with 'frontmatter', 'body', and 'raw' keys.
    """
    result: dict[str, Any] = {"raw": content, "frontmatter": {}, "body": ""}

    # Extract YAML frontmatter between --- markers
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if not fm_match:
        # No frontmatter — treat entire content as body
        result["body"] = content
        return result

    yaml_text = fm_match.group(1)
    body_text = fm_match.group(2)

    # Parse YAML frontmatter
    try:
        import yaml
        frontmatter = yaml.safe_load(yaml_text) or {}
        if not isinstance(frontmatter, dict):
            frontmatter = {}
    except Exception as e:
        logger.warning("Failed to parse SKILL.md frontmatter: %s", e)
        frontmatter = {}

    result["frontmatter"] = frontmatter
    result["body"] = body_text

    return result


def validate_skill_md(parsed: dict[str, Any]) -> list[str]:
    """Validate a parsed SKILL.md and return a list of errors.

    Args:
        parsed: Output from parse_skill_md() or parse_skill_md_content().

    Returns:
        List of error strings. Empty list means valid.
    """
    errors = []
    fm = parsed.get("frontmatter", {})

    # name is required
    name = fm.get("name", "")
    if not name:
        errors.append("缺少必填字段: name")
    else:
        if len(name) > _MAX_NAME_LEN:
            errors.append(f"name 超过最大长度 ({len(name)}/{_MAX_NAME_LEN})")
        if not _NAME_PATTERN.match(name):
            errors.append("name 只能包含小写字母、数字和连字符，且不能以连字符开头或结尾")

    # description is required
    desc = fm.get("description", "")
    if not desc:
        errors.append("缺少必填字段: description")
    elif len(desc) > _MAX_DESC_LEN:
        errors.append(f"description 超过最大长度 ({len(desc)}/{_MAX_DESC_LEN})")

    return errors


def discover_skill_type(skill_dir: str) -> str | None:
    """Discover what type of skill a directory contains.

    Args:
        skill_dir: Path to the skill directory.

    Returns:
        - "agent_skill" if SKILL.md exists (Agent Skills standard)
        - None if not a recognized skill
    """
    if os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
        return "agent_skill"
    return None


def parse_compatibility(compatibility: str) -> list[str]:
    """Parse the 'compatibility' field to extract package names.

    Examples:
      "Requires Python 3.8+ with weasyprint" -> ["weasyprint"]
      "Requires pandas and numpy" -> ["pandas", "numpy"]
      "Python 3.10+, requests, pillow" -> ["requests", "pillow"]

    Args:
        compatibility: The compatibility string from frontmatter.

    Returns:
        List of package names that need to be installed.
    """
    if not compatibility:
        return []

    # Common words to filter out
    stop_words = {
        "requires", "required", "python", "with", "and", "or",
        "needs", "need", "requires", "version", "or higher",
        "pip", "install", "must", "have", "the", "a", "an",
    }

    # Remove Python version specs like "Python 3.8+", ">=3.10", etc.
    text = re.sub(r"[Pp]ython\s*[\d\.]+\+?", "", compatibility)
    text = re.sub(r">=?\s*[\d\.]+", "", text)
    text = re.sub(r"<\s*[\d\.]+", "", text)

    # Extract potential package names (word characters, possibly with hyphens)
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]*", text)

    # Filter out stop words and very short tokens
    packages = []
    seen = set()
    for token in tokens:
        lower = token.lower()
        if lower not in stop_words and len(lower) >= 2 and lower not in seen:
            seen.add(lower)
            packages.append(lower)

    return packages
