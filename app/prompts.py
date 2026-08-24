"""Prompt formatting and template rendering for Forage."""

from __future__ import annotations

import re
from typing import Any, Dict


def render_prompt(template: str, context: Dict[str, Any]) -> str:
    """Render a prompt template replacing `{var}` placeholders with context values.

    Handles missing keys gracefully and does not fail on extra JSON/code braces.
    """
    if not template:
        return ""

    def _replace(match: re.Match) -> str:
        key = match.group(1).strip()
        val = context.get(key)
        if val is not None:
            return str(val)
        return match.group(0)

    # Replace {key} where key is a valid identifier
    rendered = re.sub(r"\{([a-zA-Z0-9_]+)\}", _replace, template)
    return rendered.strip()
