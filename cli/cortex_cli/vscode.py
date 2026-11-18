"""VS Code's Copilot reads the store without asking — on request (ADR-014 §3.5).

Measured in #137: in ``store`` and ``link``, VS Code asks once per session before Copilot reads
outside the workspace. Copilot Chat reads ``github.copilot.chat.additionalReadAccessPaths``, the
absolute folders it reads from without asking — compared as written: no ``~``, no variable, and
not a link inside the workspace that leads outside it. ``copilot_access = true`` — in
``cortex.toml`` for the team, in ``cortex.local.toml`` for one developer — has ``cortex sync`` keep
one entry there: the store's versions folder, one entry for every version, in VS Code's settings of
this machine — never the project's ``.vscode/settings.json``, which a team commits.

Which file: inside a VS Code remote — WSL, SSH, a container — the server's machine settings,
``~/.vscode-server/data/Machine/settings.json`` (and ``-insiders``'s); otherwise the user's
``settings.json`` of each VS Code installed, ``Code`` and ``Code - Insiders``.

Cortex owns the entry it wrote — ``copilot_entry`` in ``cortex.local.toml`` records it — and no
other. VS Code's settings are JSON with comments: the entry is placed, or removed, as a text edit
that keeps every other byte, then read back. A file it cannot place it in — one that does not
parse, a value that is no list — is left as it is, and never rewritten without its comments.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from .paths import display

KEY = "github.copilot.chat.additionalReadAccessPaths"
SERVERS = (".vscode-server", ".vscode-server-insiders")
DESKTOPS = ("Code", "Code - Insiders")
UNIT = "    "                       # VS Code's own indentation, for a file that shows none


class SettingsError(Exception):
    """A settings file left as it is: the message names it, and why."""


class JsoncError(ValueError):
    def __init__(self, why: str, line: int):
        super().__init__(f"{why} at line {line}")
        self.line = line


def entry_for(versions: Path) -> str:
    """The entry that lets Copilot read every version of the store: its versions folder, absolute,
    as the spec's path is written — Copilot compares the paths as written."""
    return display(os.path.abspath(versions))


def line_to_add(entry: str) -> str:
    """The line a developer adds by hand where sync cannot."""
    return f"{json.dumps(KEY)}: [{json.dumps(entry, ensure_ascii=False)}]"


def settings_files(home: Optional[Path] = None, system: Optional[str] = None,
                   environ: Optional[Mapping[str, str]] = None) -> List[Path]:
    """VS Code's settings files of this machine that Copilot reads: a remote server's machine settings
    when a server is there, else the user's settings of each desktop VS Code installed — none when
    there is neither. ``home``, ``system`` (``sys.platform``) and ``environ`` default to this machine's."""
    home = Path(home) if home is not None else Path.home()
    system = system or sys.platform
    environ = os.environ if environ is None else environ
    servers = [home / name for name in SERVERS if (home / name).is_dir()]
    if servers:
        return [server / "data" / "Machine" / "settings.json" for server in servers]
    if environ.get("VSCODE_APPDATA"):               # VS Code's own variable for where its settings are
        base = Path(environ["VSCODE_APPDATA"])
    elif system.startswith("win"):
        base = Path(environ["APPDATA"]) if environ.get("APPDATA") else home / "AppData" / "Roaming"
    elif system == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(environ["XDG_CONFIG_HOME"]) if environ.get("XDG_CONFIG_HOME") else home / ".config"
    return [base / name / "User" / "settings.json" for name in DESKTOPS if (base / name).is_dir()]


# --------------------------------------------------------------------------- #
# JSON with comments, as VS Code writes it: // and /* */ comments, trailing commas
# --------------------------------------------------------------------------- #

_TOKEN = re.compile(r"""
    (?P<space>[ \t\r\n]+)
  | (?P<comment>//[^\r\n]*|/\*.*?\*/)
  | (?P<string>"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*")
  | (?P<punct>[{}\[\]:,])
  | (?P<literal>-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?(?![0-9A-Za-z_])|(?:true|false|null)(?![0-9A-Za-z_]))
""", re.VERBOSE | re.DOTALL)


@dataclass
class _Node:
    kind: str                                   # object, array, string, literal
    start: int
    end: int                                    # past its last character
    items: List[Tuple[Optional["_Node"], "_Node", Optional[int]]] = field(default_factory=list)
    # each item: (its key — an object's — or None, its value, the position of the comma after it or None)

    def value(self, text: str) -> object:
        if self.kind == "object":
            return {json.loads(text[key.start:key.end]): node.value(text) for key, node, _ in self.items}
        if self.kind == "array":
            return [node.value(text) for _, node, _ in self.items]
        return json.loads(text[self.start:self.end])


def _line(text: str, at: int) -> int:
    return text.count("\n", 0, at) + 1


def _tokens(text: str) -> List[Tuple[str, int, int]]:
    found, at = [], 0
    while at < len(text):
        match = _TOKEN.match(text, at)
        if match is None:
            why = "a comment that never ends" if text.startswith("/*", at) else \
                "a string that never ends, or holds a character JSON refuses" if text[at] == '"' else \
                f"{json.dumps(text[at])}, which JSON does not read there"
            raise JsoncError(why, _line(text, at))
        if match.lastgroup not in ("space", "comment"):
            found.append((match.lastgroup, match.start(), match.end()))
        at = match.end()
    return found


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens = _tokens(text)
        self.at = 0

    def _peek(self) -> Optional[Tuple[str, int, int]]:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def _fail(self, why: str):
        token = self._peek()
        raise JsoncError(why, _line(self.text, token[1] if token else len(self.text)))

    def _is(self, char: str) -> bool:
        token = self._peek()
        return token is not None and token[0] == "punct" and self.text[token[1]] == char

    def _take(self) -> Tuple[str, int, int]:
        token = self._peek()
        if token is None:
            self._fail("the file ends too soon")
        self.at += 1
        return token

    def document(self) -> Optional[_Node]:
        if self._peek() is None:
            return None                         # empty, or comments only: VS Code reads no setting
        node = self.value()
        if self._peek() is not None:
            self._fail("more after the settings' object")
        return node

    def value(self, depth: int = 0) -> _Node:
        if depth > 200:
            self._fail("values nested too deep")
        kind, start, end = self._take()
        if kind in ("string", "literal"):
            return _Node(kind, start, end)
        char = self.text[start]
        if char not in "{[":
            self.at -= 1
            self._fail(f"{json.dumps(char)} where a value is expected")
        node = _Node("object" if char == "{" else "array", start, end)
        close = "}" if char == "{" else "]"
        while not self._is(close):
            key = None
            if node.kind == "object":
                token = self._peek()
                if token is None or token[0] != "string":
                    self._fail("a key, in quotes, expected")
                key = _Node("string", *self._take()[1:])
                if not self._is(":"):
                    self._fail('":" expected after a key')
                self._take()
            item = self.value(depth + 1)
            comma = None
            if self._is(","):
                comma = self._take()[1]
            elif not self._is(close):
                self._fail(f'"," or "{close}" expected')
            node.items.append((key, item, comma))
        node.end = self._take()[2]
        return node


def _parse(text: str) -> Optional[_Node]:
    root = _Parser(text).document()
    if root is not None and root.kind != "object":
        raise JsoncError("no object", _line(text, root.start))
    return root


def _member(text: str, root: Optional[_Node]) -> Optional[Tuple[_Node, _Node, Optional[int]]]:
    """The member of ``KEY`` — ``SettingsError`` when it is there twice, or is no list."""
    if root is None:
        return None
    found = [item for item in root.items if json.loads(text[item[0].start:item[0].end]) == KEY]
    if len(found) > 1:
        raise SettingsError(f"{json.dumps(KEY)} is there twice")
    if found and found[0][1].kind != "array":
        raise SettingsError(f"{json.dumps(KEY)} is no list")
    return found[0] if found else None


def entries(text: Optional[str]) -> List[object]:
    """The entries of ``KEY`` in the settings ``text`` — ``JsoncError`` or ``SettingsError`` when it
    cannot be read."""
    if text is None:
        return []
    text = text.lstrip("\ufeff")
    member = _member(text, _parse(text))
    return member[1].value(text) if member else []    # type: ignore[return-value]


def _indent_of(text: str, at: int) -> Optional[str]:
    """The blanks before ``at`` on its line, when only blanks are there."""
    start = text.rfind("\n", 0, at) + 1
    before = text[start:at]
    return before if before.strip(" \t") == "" else None


def _first_newline(text: str, start: int, end: int) -> Optional[int]:
    """The first line break between ``start`` and ``end`` outside a comment — ``None`` when there is none."""
    at = start
    while at < end:
        match = _TOKEN.match(text, at)
        if match is None or match.lastgroup not in ("space", "comment"):
            return None
        if match.lastgroup == "space":
            for index in range(match.start(), min(match.end(), end)):
                if text[index] in "\r\n":
                    return index
        at = match.end()
    return None


def _insert(text: str, container: _Node, unit: str, newline: str, first: str, rest: List[str], inline: str,
            outer: str, expand: bool = True) -> List[Tuple[int, int, str]]:
    """The edits that add an item to ``container`` — an object or an array — as its last: ``first``
    on the first line, then the lines of ``rest``, each indented as the item, or ``inline`` in a
    container written on one line — but an empty one ``expand`` opens on lines of its own. ``outer``,
    the indent of the line that holds the container's opening."""
    edits: List[Tuple[int, int, str]] = []
    close = container.end - 1
    if container.items:
        key, node, comma = container.items[-1]
        anchor = comma + 1 if comma is not None else node.end
        if comma is None:
            edits.append((node.end, node.end, ","))
        last_start = (key or node).start
    else:
        anchor, last_start = container.start + 1, None
    multiline = any(char in "\r\n" for char in text[container.start:container.end])
    if not multiline and (container.items or not expand):
        edits.append((close, close, (" " if container.items else "") + inline))
        return edits
    indent = _indent_of(text, last_start) if last_start is not None else None
    if indent is None:
        indent = outer + unit
    block = indent + first + "".join(newline + indent + line for line in rest)
    if container.items and container.items[-1][2] is not None:
        block += ","                            # a file that ends each item with a comma: this one too
    if not multiline:                          # the settings' own {}, opened on lines of its own
        edits.append((container.start + 1, close, newline + block + newline + outer))
        return edits
    at = _first_newline(text, anchor, close)
    if at is not None:
        edits.append((at, at, newline + block))
    else:
        edits.append((close, close, newline + block + newline + (_indent_of(text, close) or outer)))
    return edits


def _remove(text: str, container: _Node, index: int) -> List[Tuple[int, int, str]]:
    """The edits that take the item ``index`` out of ``container``, its comma with it, and its line when
    nothing else is on it — a comment beside it stays."""
    key, node, comma = container.items[index]
    start, end = (key or node).start, (comma + 1 if comma is not None else node.end)
    edits: List[Tuple[int, int, str]] = []
    if comma is None and index > 0:
        previous = container.items[index - 1][2]
        if previous is not None and text[previous + 1:start].strip(" \t") == "":
            start = previous                    # "a", "e" → "a"
        elif previous is not None:
            edits.append((previous, previous + 1, ""))
    line_start = text.rfind("\n", 0, start) + 1
    line_end = len(text)
    for index_end in range(end, len(text)):
        if text[index_end] in "\r\n":
            line_end = index_end
            break
    if text[line_start:start].strip(" \t") == "" and text[end:line_end].strip(" \t") == "" \
            and line_end < len(text):
        stop = line_end + (2 if text.startswith("\r\n", line_end) else 1)
        edits.append((line_start, stop, ""))
    else:
        after = end
        while comma is not None and after < len(text) and text[after] in " \t":
            after += 1
        if comma is not None and (after >= len(text) or text[after] in "\r\n/"):
            after = end                         # blanks before a comment, or the line's end, stay
        edits.append((start, after, ""))
    return edits


def _apply(text: str, edits: List[Tuple[int, int, str]]) -> str:
    """The edits made from the last: two at one place are in the text in the order they were listed."""
    ordered = sorted(enumerate(edits), key=lambda item: (item[1][0], item[1][1], item[0]), reverse=True)
    for _, (start, end, inserted) in ordered:
        text = text[:start] + inserted + text[end:]
    return text


def _unit(text: str, root: Optional[_Node]) -> str:
    if root is not None:
        for key, _, _ in root.items:
            indent = _indent_of(text, key.start)            # type: ignore[union-attr]
            if indent:
                return indent
    return UNIT


def with_entry(text: Optional[str], entry: str) -> str:
    """``text`` with ``entry`` added as the last of ``KEY``'s list — the key added when it is not there,
    the file made when ``text`` is ``None``. Every other byte kept."""
    bom = "\ufeff" if text is not None and text.startswith("\ufeff") else ""
    body = (text or "")[len(bom):]
    newline = "\r\n" if "\r\n" in body else "\n"
    root = _parse(body)
    unit = _unit(body, root)
    value = json.dumps(entry, ensure_ascii=False)
    member = _member(body, root)
    if root is None:
        lead = newline if body and not body.endswith(("\n", "\r")) else ""
        added = (f"{lead}{{{newline}{unit}{json.dumps(KEY)}: [{newline}{unit}{unit}{value}{newline}"
                 f"{unit}]{newline}}}{newline}")
        return bom + body + added
    if member is None:
        edits = _insert(body, root, unit, newline, f"{json.dumps(KEY)}: [", [unit + value, "]"],
                        f"{json.dumps(KEY)}: [{value}]", _indent_of(body, root.start) or "")
    else:
        array = member[1]
        outer = _indent_of(body, member[0].start) or ""     # type: ignore[union-attr]
        edits = _insert(body, array, unit, newline, value, [], value, outer, expand=False)
    return bom + _apply(body, edits)


def without_entry(text: str, entry: str) -> str:
    """``text`` without the entries of ``KEY`` that are ``entry``, as written — and without the key,
    when its list is left empty and holds no comment. Every other byte kept."""
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    body = text[len(bom):]
    root = _parse(body)
    member = _member(body, root)
    if member is None:
        return text
    array = member[1]
    for index in reversed(range(len(array.items))):
        node = array.items[index][1]
        if node.kind == "string" and json.loads(body[node.start:node.end]) == entry:
            body = _apply(body, _remove(body, array, index))
            root = _parse(body)
            member = _member(body, root)
            array = member[1]                               # type: ignore[index]
    if not array.items and "/" not in body[array.start:array.end]:
        index = next(number for number, item in enumerate(root.items) if item[1].start == array.start)  # type: ignore[union-attr]
        body = _apply(body, _remove(body, root, index))     # type: ignore[arg-type]
    return bom + body


def covers(entry: object, path: str) -> bool:
    """Whether ``entry`` lets Copilot read ``path``: the same folder, or one that holds it, as written —
    Copilot neither expands ``~`` nor follows a link."""
    if not isinstance(entry, str) or not os.path.isabs(entry):
        return False
    folder = os.path.normcase(os.path.normpath(entry))
    target = os.path.normcase(os.path.normpath(path))
    return target == folder or target.startswith(folder.rstrip(os.sep) + os.sep)


# --------------------------------------------------------------------------- #
# A settings file
# --------------------------------------------------------------------------- #

def _read(path: Path) -> Optional[str]:
    real = Path(os.path.realpath(path))
    if not real.exists():
        if os.path.lexists(path) and not os.path.islink(path):
            raise SettingsError(f"{display(str(path))} is no file: left as it is")
        return None                             # none yet, or a link that leads nowhere: written there
    if not real.is_file():
        raise SettingsError(f"{display(str(path))} is no file: left as it is")
    try:
        return real.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SettingsError(f"{display(str(path))} cannot be read ({getattr(error, 'strerror', None) or error}): "
                            "left as it is")


def _write(path: Path, text: str) -> None:
    real = Path(os.path.realpath(path))         # a link — to a dotfiles repository — is written through
    try:
        real.parent.mkdir(parents=True, exist_ok=True)
        temporary = real.with_name(f"{real.name}.{os.getpid()}.tmp")
        with open(temporary, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if real.exists():
            shutil.copymode(real, temporary)
        os.replace(temporary, real)
    except OSError as error:
        if "temporary" in locals() and os.path.exists(temporary):
            os.unlink(temporary)
        raise SettingsError(f"{display(str(path))} cannot be written ({error.strerror or error}): left as it is")


def allows(path: Path, target: str) -> bool:
    """Whether an entry of the file lets Copilot read ``target`` — ``False`` when it cannot be read."""
    try:
        return any(covers(entry, target) for entry in entries(_read(path)))
    except (SettingsError, JsoncError):
        return False


def update(path: Path, allow: Optional[str], owned: Optional[str]) -> Tuple[Optional[str], bool]:
    """Make ``allow`` — or nothing, when it is ``None`` — the entry Cortex keeps in the settings file
    ``path``. ``owned`` is the entry Cortex wrote before, the only one it may remove.

    Return what to report — ``None`` when nothing changed — and whether Cortex owns ``allow`` in the
    file: ``False`` when an entry of the developer's already lets Copilot read it. ``SettingsError``
    for a file left as it is."""
    shown = display(str(path))
    text = _read(path)
    try:
        before = entries(text)
        keep_owned = owned is not None and owned == allow
        written = text
        removed = owned if owned is not None and not keep_owned and owned in before else None
        if removed is not None:
            written = without_entry(written, removed)       # type: ignore[arg-type]
        owns = False
        if allow is not None:
            now = entries(written)
            if allow in now:
                owns = keep_owned                           # Cortex's before, or the developer's
            elif not any(covers(entry, allow) for entry in now):
                written = with_entry(written, allow)
                owns = True
        if written == text:
            return None, owns
        _check(text, written, allow if owns and allow not in before else None, removed)
    except JsoncError as error:
        raise SettingsError(f"{shown} is no JSON with comments cortex can read ({error}): left as it is")
    except SettingsError as error:
        raise SettingsError(f"{shown}: {error}: left as it is")
    if text is not None and not os.access(os.path.realpath(path), os.W_OK):
        raise SettingsError(f"{shown} is read-only: left as it is")
    _write(path, written)                                   # type: ignore[arg-type]
    if allow is not None and owns:
        return f"{shown}: Copilot reads {allow} without asking", True
    if allow is not None:
        return (f"{shown}: the entry cortex sync wrote is removed — an entry of your own lets Copilot read "
                f"{allow} without asking"), False
    return f"{shown}: Copilot no longer reads the store without asking — the entry cortex sync wrote is removed", False


def _check(text: Optional[str], written: str, added: Optional[str], removed: Optional[str]) -> None:
    """Read back what is to be written: every setting as it was, but ``KEY``'s list, with ``added``
    last and without ``removed`` — an edit that reads otherwise is never written. An empty list and
    no list read alike."""
    def settings(source: Optional[str]) -> Dict:
        body = (source or "").lstrip("\ufeff")
        root = _parse(body)
        found: Dict = root.value(body) if root is not None else {}     # type: ignore[assignment]
        if found.get(KEY) == []:
            found.pop(KEY)
        return found

    expected = settings(text)
    listed = [entry for entry in expected.get(KEY, []) if entry != removed]
    if added is not None:
        listed.append(added)
    expected.pop(KEY, None)
    if listed:
        expected[KEY] = listed
    if settings(written) != expected:
        raise SettingsError("the edit would not read back as written — a bug of cortex")
