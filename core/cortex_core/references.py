"""What a project's file refers to, and whether it is there — ``REFERENCE_NOT_FOUND`` (ADR-007, #140).

``cortex validate`` reads every file of a project's layer directories: overlays, and custom additions,
the files with no base. What such a file names — a file of the spec, a role, a character, a workflow —
fails only at the first conversation that follows it, when the Prompt Manager looks for it. This module
finds those names in the forms a file dispatches with, and says which of them name nothing.

The rule is narrow on purpose: a reference it misses costs less than a warning that cries wolf over a
mention that is no reference. What counts, outside fenced code blocks and HTML comments:

- **Spec paths** — ``cortex/agents/…``, ``cortex/docs/…``, ``cortex/templates/…``: a file or a
  directory of the spec.
- **Layer paths** — ``agents/{layer}/…``, and ``roles/…`` and ``workflows/…`` written from the layer
  directory, as the base's workflows dispatch: a file or a directory of a tier of the file's own cascade
  — the spec, the team's folder, and the file's service when it is a service's. A workflow in any of
  them is the one the cascade serves, by replacement: one is enough.
- **Roles** written ``{category}/{role}``, in backticks, on a dispatch line — ``**Agent:**``,
  ``**Agents:**`` — or just before a character in parentheses: a role of one of those tiers.
- **Characters** — ``@Name``, capitalised, only where it is tied to a role: in parentheses right after a
  role — ``roles/data/data-analyst.md`` (@Frankie-Benjy), `` `prompt-manager` (@Oolon)`` — or followed by
  one — ``**@Marvin** (security-engineer)``. A bare role counts there only when it names a role that
  exists. The character must be in the theme's ``characters.md``.

What never counts: a path with a placeholder — ``{service}``, ``<name>``, ``*``, ``…`` — which is a
pattern; a path preceded by another path's characters — ``{service}/agents/…``, ``../roles/…``, a
URL's ``…/cortex/agents/…``; an ``@`` mention anywhere else — Claude Code's subagents such as ``@Explore``,
a service's alias such as ``@nexus``, an e-mail address, an npm scope such as ``@types/node``; and any
path outside the spec's ``agents/``, ``docs/`` and ``templates/`` and the four layers — ``cortex.toml``,
``project-context.md``.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, List, NamedTuple, Optional, Sequence, Set

# A path's own characters: what may stand just before a reference that is one — then it is part of
# another path, a URL or a placeholder's, and no reference of its own.
_PATH_CHAR = r"A-Za-z0-9._/{}\\~$-"
# What a path runs to: up to a space, a quote, a bracket or a table's bar.
_TOKEN = r"[^\s`'\"()\[\]|,;]*"
_SPEC = re.compile(rf"(?<![{_PATH_CHAR}])cortex/(agents|docs|templates)(/{_TOKEN}|(?![^\s`'\"()\[\]|,;]))")
_LAYER = re.compile(rf"(?<![{_PATH_CHAR}])(agents/)?(roles|capabilities|personalities|workflows)/({_TOKEN})")
_PLACEHOLDER = re.compile(r"[{}<>*$…]|\.\.\.|\\")
_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_ROLE_NAME = re.compile(rf"(?:{_SLUG}/)?{_SLUG}")
_DISPATCH = re.compile(r"\*\*\s*Agents?\s*:\s*\*\*|\*\*\s*Agents?\s*\*\*\s*:", re.IGNORECASE)
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_NAME = r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*"
# `role` (@Name) — the role in backticks, or a layer path, then the character in parentheses
_AFTER_ROLE = re.compile(rf"(?:`([^`\n]+)`|(?<![{_PATH_CHAR}])((?:agents/)?roles/[^\s`'\"()\[\]|,;]+))\s*\(@({_NAME})\)")
# @Name (role) — the character, in bold or not, then a role in parentheses
_BEFORE_ROLE = re.compile(rf"(?<![\w@./`])@({_NAME})(?:\*\*|__)?\s*\(\s*`?({_SLUG}(?:/{_SLUG})?)`?\s*\)")
_ALIAS = re.compile(r"(?<![\w@./])@([A-Za-z][A-Za-z0-9_-]*)")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


class Reference(NamedTuple):
    line: int
    kind: str           # "spec", "layer", "role", "character"
    name: str           # as written


class Missing(NamedTuple):
    line: int
    message: str


def prose_lines(text: str) -> List[str]:
    """``text``'s lines, with fenced code blocks and HTML comments blanked out — a line kept for each,
    so that line numbers stay the file's. A CRLF checkout reads the same: a carriage return ends no path,
    and closes no fence."""
    # HTML comments, each character but the line breaks made a space
    text = re.sub(r"<!--.*?(?:-->|\Z)", lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.DOTALL)
    lines, fence = [], None
    for line in text.split("\n"):
        opened = _FENCE.match(line)
        if fence is None and opened:
            fence = opened.group(1)
            lines.append("")
        elif fence is not None:
            closing = _FENCE.match(line)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence) \
                    and not line.strip()[len(closing.group(1)):].strip():
                fence = None
            lines.append("")
        else:
            lines.append(line)
    return lines


def _clean(path: str) -> Optional[str]:
    """``path`` as a reference names it — its anchor, its line number and the sentence's punctuation
    off — or ``None`` when it is a pattern."""
    path = path.rstrip(".:!?*_")           # the sentence's, or the emphasis's
    if _PLACEHOLDER.search(path):
        return None
    path = path.split("#", 1)[0]
    return re.sub(r":\d+(?:[-:]\d+)*$", "", path)


def _roles_on_dispatch(line: str) -> Iterable[str]:
    """The roles a dispatch line names, ``{category}/{role}`` in backticks — up to its next bold label."""
    for match in _DISPATCH.finditer(line):
        rest = line[match.end():]
        rest = rest.split("**", 1)[0]
        for code in _BACKTICKED.finditer(rest):
            name = code.group(1).strip()
            if "/" in name and _ROLE_NAME.fullmatch(name):
                yield name


def references(text: str, role_exists) -> List[Reference]:
    """Every reference of ``text``, in order and once each. ``role_exists(name)`` says whether a bare
    role — ``lead-backend``, ``product/market-analyst`` — names one: a character is tied only to a role
    that is one, when the role is written bare."""
    found: List[Reference] = []
    seen: Set[tuple] = set()

    def add(number: int, kind: str, name: str) -> None:
        if (kind, name) not in seen:
            seen.add((kind, name))
            found.append(Reference(number, kind, name))

    for number, line in enumerate(prose_lines(text), 1):
        for match in _SPEC.finditer(line):
            path = _clean(f"cortex/{match.group(1)}{match.group(2)}")
            if path is not None:
                add(number, "spec", path)
        for match in _LAYER.finditer(line):
            prefix, layer, rest = match.group(1), match.group(2), match.group(3)
            if not prefix and layer not in ("roles", "workflows"):
                continue            # capabilities/… and personalities/… are named from agents/ only
            path = _clean(f"{prefix or ''}{layer}/{rest}")
            if path is not None and not path.endswith(f"{layer}/"):
                add(number, "layer", path)
        for name in _roles_on_dispatch(line):
            add(number, "role", name)
        for match in _AFTER_ROLE.finditer(line):
            code, path, character = match.group(1), match.group(2), match.group(3)
            if path is not None:
                tied = _clean(path) is not None
            else:
                name = code.strip()
                tied = bool(re.fullmatch(r"(?:agents/)?roles/\S+", name)) and _clean(name) is not None
                if not tied and _ROLE_NAME.fullmatch(name):
                    if "/" in name:
                        add(number, "role", name)
                        tied = True
                    else:
                        tied = role_exists(name)
            if tied:
                add(number, "character", character)
        for match in _BEFORE_ROLE.finditer(line):
            if role_exists(match.group(2)):
                add(number, "character", match.group(1))
    return found


def aliases(text: str) -> Set[str]:
    """The characters a ``characters.md`` names: every ``@Name`` of its table's rows."""
    names: Set[str] = set()
    for line in prose_lines(text):
        if line.lstrip().startswith("|"):
            names.update(match.group(1) for match in _ALIAS.finditer(line))
    return names


class Tiers:
    """The layer directories of one file's cascade, base first, and what they hold."""

    def __init__(self, base_root: str, layer_dirs: Sequence[str], theme: Optional[str]):
        self.base_root = base_root
        self.dirs = [f"{base_root}/agents", *layer_dirs]
        self.theme = theme
        self._characters: Optional[Set[str]] = None
        self._read = False

    def has(self, path: str) -> bool:
        return any(os.path.exists(f"{tier}/{path}") for tier in self.dirs)

    def role_exists(self, name: str) -> bool:
        """Whether ``name`` — ``{category}/{role}``, or a bare role — is a role of a tier."""
        if "/" in name:
            return self.has(f"roles/{name}.md")
        for tier in self.dirs:
            roles = f"{tier}/roles"
            if os.path.isfile(f"{roles}/{name}.md"):
                return True
            try:
                categories = os.listdir(roles)
            except OSError:
                continue
            if any(os.path.isfile(f"{roles}/{category}/{name}.md") for category in categories):
                return True
        return False

    def characters(self) -> Optional[Set[str]]:
        """The characters of the theme — of every theme of the tiers when none is given — or ``None``
        when there is none to check against: the theme ``none``, or a theme no tier holds."""
        if self._characters is None:
            files: List[str] = []
            if self.theme is None:
                for tier in self.dirs:
                    try:
                        themes = sorted(os.listdir(f"{tier}/personalities"))
                    except OSError:
                        continue
                    files += [f"{tier}/personalities/{t}/characters.md" for t in themes]
            else:                       # the theme none has no characters.md: nothing to check
                files = [f"{tier}/personalities/{self.theme}/characters.md" for tier in self.dirs]
            names: Set[str] = set()
            read = False
            for path in files:
                try:
                    with open(path, "rb") as fh:
                        names |= aliases(fh.read().decode("utf-8", "surrogateescape"))
                    read = True
                except OSError:
                    continue
            self._characters = names if read else set()
            self._read = read
        return self._characters if self._read else None


_CASCADE = "of this file's cascade"


def missing(text: str, tiers: Tiers) -> List[Missing]:
    """What ``text`` refers to and no tier holds, in the file's order."""
    found: List[Missing] = []
    for ref in references(text, tiers.role_exists):
        if ref.kind == "spec":
            if not os.path.exists(f"{tiers.base_root}/{ref.name[len('cortex/'):]}"):
                found.append(Missing(ref.line, f"line {ref.line}: '{ref.name}' is no file of the spec"))
        elif ref.kind == "layer":
            path = ref.name[len("agents/"):] if ref.name.startswith("agents/") else ref.name
            if not tiers.has(path):
                found.append(Missing(ref.line, f"line {ref.line}: '{ref.name}' is in neither the spec nor an "
                                               f"overlay tier {_CASCADE}"))
        elif ref.kind == "role":
            if not tiers.role_exists(ref.name):
                found.append(Missing(ref.line, f"line {ref.line}: the role '{ref.name}' is in neither the spec "
                                               f"nor an overlay tier {_CASCADE} — no roles/{ref.name}.md"))
        elif ref.kind == "character":
            names = tiers.characters()
            if names is not None and ref.name not in names:
                said = (f"is no character of the theme '{tiers.theme}' — its characters.md does not name it"
                        if tiers.theme is not None else "is named by no theme's characters.md")
                found.append(Missing(ref.line, f"line {ref.line}: '@{ref.name}' {said}"))
    return found
