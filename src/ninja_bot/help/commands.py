"""Plain metadata of registered commands and the usage/parameter lines built from it.

The cog converts the live app-command tree into `CommandInfo`; this module never imports
discord, so the formatting stays pure and testable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

MAX_PARAMETER_LINE = 300
ELLIPSIS = "…"


@dataclass(frozen=True)
class ParamInfo:
    name: str
    description: str
    required: bool
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandInfo:
    name: str  # Without the leading `/`.
    description: str
    params: tuple[ParamInfo, ...] = ()


def _usage_part(param: ParamInfo) -> str:
    value = "|".join(param.choices) if param.choices else f"<{param.name}>"
    part = f"{param.name}:{value}"
    return part if param.required else f"[{part}]"


def usage_line(info: CommandInfo) -> str:
    """`/name` followed by the parameters in order, e.g. `/download url:<url> [format:mp3|mp4]`."""
    return " ".join([f"/{info.name}", *(_usage_part(param) for param in info.params)])


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(ELLIPSIS)].rstrip() + ELLIPSIS


def parameter_lines(info: CommandInfo, notes: Mapping[str, str]) -> list[str]:
    """One line per parameter: requirement, description, choices and the extra note."""
    lines: list[str] = []
    for param in info.params:
        kind = "required" if param.required else "optional"
        line = f"{param.name} ({kind}): {param.description}"
        if param.choices:
            line += f" Choices: {', '.join(param.choices)}"
        note = notes.get(param.name)
        if note:
            line += f" {note}"
        lines.append(_cut(line, MAX_PARAMETER_LINE))
    return lines
