"""``cortex init`` — the setup script of Cortex 0.x, at parity (ADR-008 §3.7).

Same options, the same defaults for a new project (``--theme h2g2``, ``--tool copilot``), same files at the same paths
as the script: the tool's instructions file, the root ``project-overview.md`` and
``project-context.md`` when missing, and in workspace mode a pair per service carrying its
``@alias`` — the basename of its folder — plus the team tier when ``agents/`` is its own git
working tree. The files are written byte for byte as the script wrote them, from the templates of
the version the project pins, in the store.

It also writes ``cortex.toml`` at the binary's own version, runs ``cortex sync``, then adds to
``.gitignore`` what git does not ignore yet: ``cortex.local.toml`` — and, in ``link`` and ``copy``
modes, ``/cortex`` and ``/.cortex-sync-*``, what a sync killed half-way leaves beside it. Outside a
git repository no ``.gitignore`` is created, and a note says so. In a project that has one,
``cortex.toml`` keeps its version, and changes only for the options given: ``--theme`` or
``--no-personality``, ``--link`` or ``--copy``.

A kept instructions file is read through the marker the templates carry under their title
(ADR-010 §3.1): the layout it was written for, and the format of its contract. A version whose
templates carry no marker yet keeps the checks it had. The file written carries in its marker the
digest of what was written, by which ``cortex upgrade`` knows a file nobody changed since.

Two deliberate differences: services are named by a repeatable ``--service``, the interactive
prompt remaining only when stdin is a terminal and none was given; and an existing instructions
file is kept unless ``--force``, instead of a ``y/N`` prompt that blocks every unattended run —
``--force`` keeps the file it replaces as ``FILE.bak`` — ``FILE.bak.N`` when a backup of other content
is there: none is overwritten.

Everything is checked before anything of the project is written: a refusal leaves it as it was.
Once all of it is written, the project is registered in the machine's ``projects.toml`` when it is
not listed there, and said once — not by the sync it runs, which may succeed where the rest fails
(ADR-011 §3.3).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import List, NamedTuple, Optional, TextIO, Tuple

from cortex_core.project import is_theme

from . import config, console, store, sync
from .paths import display, working_directory

TOOLS = {
    "copilot": ".github/copilot-instructions.md",
    "cursor": ".cursor/rules/cortex.mdc",
    "claude": "CLAUDE.md",
    "agents": "AGENTS.md",
}
ALIAS_PLACEHOLDER = b"<!-- @alias: my-project -->"
PERSONALITY_BEGIN = b"<!-- PERSONALITY:BEGIN -->"
TITLE = b"# Cortex AI Team"             # how the templates' heading begins, either layout's
PERSONALITY_END = b"<!-- PERSONALITY:END -->"
# What some editors put before the first line of a UTF-8 file: no part of what it says.
BOM = b"\xef\xbb\xbf"
# The marker a bootstrap template carries under its title (ADR-010 §3.1): a whole line, read anywhere.
# The file Cortex writes from it carries the digest of what it wrote; the templates, none.
BOOTSTRAP_MARKER = re.compile(rb"(?m)^<!-- cortex:bootstrap layout=(single|workspace) format=([1-9][0-9]*)"
                              rb"(?: digest=([0-9a-f]{16}))? -->\r*$")
DIGEST_FIELD = b" digest="
# The line that closes Cortex's block in a bootstrap of format 2 (ADR-012 §3.1): what follows it is the team's.
END_MARKER = re.compile(rb"(?m)^<!-- cortex:bootstrap end -->\r*$")
# A line break as the digest and the markers read it: CR LF, or the CR CR LF a Windows script leaves when it
# writes text over CR LF — any run of CRs before an LF. One reading for both, or they split a file apart.
LINE_BREAK = re.compile(rb"(?<!\r)\r+\n")      # from the first CR of a run: linear on a long one
# What a Cursor rule opens on, that Cursor applies it in every conversation. Its documentation applies a
# rule with `alwaysApply: false`, no `description` and no `globs` only where a chat @-mentions it; one with
# no frontmatter, inferred to be read the same, is still to see in a real conversation (#306, #137).
CURSOR_RULE = b"---\ndescription: Cortex - the AI team's bootstrap, read first\nalwaysApply: true\n---\n"
# Each line read one way only: `.` takes a CR already, and a line that could end on `.*` or on `\r?` makes an
# unclosed frontmatter of CRLF lines take for ever to refuse.
FRONTMATTER = re.compile(rb"\A---\r?\n(?:.*\n)*?---\r?\n")


class InitError(Exception):
    pass


# --------------------------------------------------------------------------- #
# The files, byte for byte as the setup script wrote them
# --------------------------------------------------------------------------- #

def instructions(template: bytes, personality: bool) -> bytes:
    """The instructions file: ``echo "$(cat template)"`` — trailing newlines trimmed, one added —
    with the personality block deleted as ``sed '/BEGIN/,/END/d'`` deletes it."""
    if not personality:
        kept, in_block = [], False
        for line in template.split(b"\n"):
            if in_block:
                in_block = PERSONALITY_END not in line
            elif PERSONALITY_BEGIN in line:
                in_block = True             # sed does not look for the end on the line that starts it
            else:
                kept.append(line)
        template = b"\n".join(kept)
    return template.rstrip(b"\n") + b"\n"


def with_alias(template: bytes, alias: str) -> bytes:
    """``sed "s|<!-- @alias: my-project -->|<!-- @alias: ALIAS -->|"`` — once per line."""
    marker = f"<!-- @alias: {alias} -->".encode("utf-8")
    return b"".join(line.replace(ALIAS_PLACEHOLDER, marker, 1) for line in template.splitlines(keepends=True))


def is_git_working_tree(directory: Path) -> bool:
    """``agents/`` is its own git working tree: it holds a ``.git`` — a directory, or the file a
    worktree or a submodule has. The setup script asked ``git -C agents rev-parse``, which is also true
    of any ``agents/`` inside the project's own repository (ADR-008 §9)."""
    return (directory / ".git").exists()


def service_path(name: str) -> str:
    """A service is a folder of the workspace: relative, never climbing out of it. A backslash
    separates folders on Windows only: elsewhere it is a character of the name."""
    path = PurePosixPath(name.replace("\\", "/") if os.name == "nt" else name)
    if not name or path.is_absolute() or ".." in path.parts or (len(name) > 1 and name[1] == ":"):
        raise InitError(f"--service {name}: a service is a folder inside the workspace")
    return str(path)


def ask(out: TextIO, question: str) -> bool:
    """A yes-or-no on the terminal, no by default: Enter, and a stdin that ends before any answer,
    say no — the question grants a permission."""
    out.write(question)
    out.flush()
    line = sys.stdin.readline()
    out.write("\n" if not line.endswith("\n") else "")
    return line.strip().lower() in ("y", "yes")


def prompt_services(out: TextIO) -> List[str]:
    out.write("   Enter the names of the services to create (empty entry to stop):\n")
    names = []
    while True:
        out.write("   Service name (e.g. api-backend, front-web): ")
        out.flush()
        line = sys.stdin.readline()
        if not line or not line.strip():
            break
        names.append(line.strip())
    return names


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #

def _same_name(path: str) -> str:
    """A path compared as a file system that ignores case compares it — macOS's and Windows'."""
    return os.path.normcase(os.path.abspath(path)).casefold()


def _refuse_instructions_file(root: Path, path: Path) -> None:
    """``--instructions-file`` names the tool's file: never a directory, never a file cortex init
    writes for itself — a project's or a service's — never the spec, nor ``cortex`` itself, nor
    anything of a git repository's own."""
    if path.is_dir():
        raise InitError(f"--instructions-file {display(str(path))}: a directory")
    own = {_same_name(root / name) for name in (config.PROJECT_FILE, config.LOCAL_FILE, ".gitignore", sync.LINK)}
    target = _same_name(path)
    spec = _same_name(root / sync.LINK)
    if target in own or target.startswith(spec + os.sep) or \
            path.name.casefold() in ("project-overview.md", "project-context.md"):
        raise InitError(f"--instructions-file {display(str(path))}: a file cortex init writes for itself — "
                        "name the tool's instructions file")
    if any(part.casefold() == ".git" for part in Path(os.path.abspath(path)).parts):
        raise InitError(f"--instructions-file {display(str(path))}: a file of git's own — name the tool's "
                        "instructions file")


def without_bom(content: bytes) -> bytes:
    """A kept file as it reads: without the byte order mark an editor may have put before its first line."""
    return content[len(BOM):] if content.startswith(BOM) else content


def without_frontmatter(content: bytes) -> bytes:
    """A kept file as the templates wrote it: without the byte order mark, and without the frontmatter
    a Cursor rule opens on."""
    content = without_bom(content)
    found = FRONTMATTER.match(content)
    return content[found.end():] if found else content


def for_tool(path: Path, content: bytes) -> bytes:
    """A bootstrap as the tool whose file ``path`` is reads it: a Cursor rule, ``.mdc``, opens on the
    frontmatter that has Cursor apply it in every conversation (#306). The digest covers it."""
    return CURSOR_RULE + content if is_cursor_rule(path) else content


def is_cursor_rule(path: Path) -> bool:
    return path.suffix == ".mdc"


def applies_always(content: bytes) -> bool:
    """Whether a Cursor rule says, in its frontmatter, that Cursor applies it in every conversation."""
    found = FRONTMATTER.match(without_bom(content))
    # The form Cursor's documentation shows — the key, blanks, `true` — and no other: whether Cursor reads
    # `True` or a comment after it as YAML would is not known (#137), and a note too many costs less than a rule
    # left out unsaid.
    return found is not None and re.search(rb"(?m)^alwaysApply:[ \t]+true[ \t]*\r?$", found.group()) is not None


def bootstrap_of_cortex(content: bytes) -> bool:
    """A file the templates wrote: their heading — below blank lines and comments a team wrote above it too — or
    their personality block."""
    return title_line(content) is not None or PERSONALITY_BEGIN in content


def title_line(content: bytes) -> Optional[bytes]:
    """The templates' heading in a bootstrap: the first line of Cortex's block, once a frontmatter is set aside, or
    one below blank lines and comments a team wrote above it; ``None`` in a file without it — or with it below a
    line of text, an example in a team's notes, say. One reading for every caller: whether a file is Cortex's, and
    the layout it was written for (ADR-010 §3.1)."""
    comment = False
    for line in without_frontmatter(parts(normalised(content))[0]).split(b"\n"):
        if not comment and line.startswith(TITLE):
            return line
        rest = line
        while True:
            if comment:
                end = rest.find(b"-->")
                if end < 0:
                    break
                rest, comment = rest[end + 3:], False
            rest = rest.strip()
            if not rest:
                break
            if not rest.startswith(b"<!--"):
                return None
            rest, comment = rest[4:], True
    return None


def reads_the_local_file(content: bytes) -> bool:
    """A bootstrap of this cortex's templates, which read ``spec`` in ``cortex.local.toml``. An
    older one reads ``cortex/`` as the submodule it was, and finds no spec once it is gone."""
    return config.LOCAL_FILE.encode() in content


def parts(content: bytes) -> Tuple[bytes, bytes]:
    """Cortex's block and the team's part of a bootstrap (ADR-012 §3.1): the block runs to the end of the
    first end marker's line after the marker, and the rest is the team's. A file with no end marker after a
    marker is all Cortex's block."""
    begin = BOOTSTRAP_MARKER.search(content)
    end = END_MARKER.search(content, begin.end()) if begin else None
    if end is None:
        return content, b""
    cut = end.end() + content[end.end():end.end() + 1].count(b"\n")
    return content[:cut], content[cut:]


def markers(content: bytes) -> List[Tuple[str, int]]:
    """The bootstrap markers of a file, ``(layout, format)`` each: one in a bootstrap. Those of the team's
    part are its text."""
    return [(m.group(1).decode(), int(m.group(2))) for m in BOOTSTRAP_MARKER.finditer(parts(without_bom(content))[0])]


def written_for_workspace(content: bytes) -> bool:
    """Whether a bootstrap was written for a workspace: by its markers when they agree, or, written before the
    marker or with markers that disagree, by its title (ADR-010 §3.1) — the templates' heading, the first line once
    a frontmatter is set aside, or one below blank lines and comments a team wrote above it, never one below a
    line of text (``title_line``). A file without that heading or the markers was written for none."""
    content = normalised(content)
    layouts = {layout for layout, _ in markers(content)}
    if len(layouts) == 1:
        return layouts == {"workspace"}
    return b"Workspace Mode" in (title_line(content) or b"")


def tool_options(path: Path, tool: Optional[str] = None) -> str:
    """The options that name the instructions file at ``path`` to ``cortex init``, run from its project's root:
    ``--tool X``, or ``--tool custom --instructions-file PATH`` for a file of no known tool's. A note names them, so
    that the command it names writes that file — without them, ``--force`` is refused where several instructions
    files are, and a project left with none writes Copilot's. ``tool`` is the run's own, when it knows it."""
    if tool is None:
        for name, rel in TOOLS.items():
            depth = len(PurePosixPath(rel).parts)
            if len(path.parts) > depth and path.parents[depth - 1] / rel == path and \
                    (path.parents[depth - 1] / config.PROJECT_FILE).is_file():
                tool = name
                break
    if tool is None or tool == "custom":
        return f"--tool custom --instructions-file {sync.quote(display(str(path)))}"
    return f"--tool {tool}"


def normalised(content: bytes) -> bytes:
    """A bootstrap as its digest reads it: without a leading byte order mark, with LF line endings —
    any run of CRs before an LF read as one (``LINE_BREAK``) — and one newline at the end, as
    ``instructions`` writes it: git may check it out with CRLF, an editor may add the mark, or take the
    last newline away."""
    return LINE_BREAK.sub(b"\n", without_bom(content)).rstrip(b"\n") + b"\n"


def _digest(content: bytes) -> bytes:
    return hashlib.sha256(content).hexdigest()[:16].encode()


def stamped(content: bytes) -> bytes:
    """A bootstrap as Cortex writes it: ``content``, written from a template, with the digest of its
    block in its marker (ADR-010 §3.1, ADR-012 §3.2) — the first 16 hexadecimal digits of the SHA-256 of
    Cortex's block, normalised, its marker without the digest. Left as it is without one marker to carry it."""
    block, team = parts(content)
    found = list(BOOTSTRAP_MARKER.finditer(block))
    if len(found) != 1 or found[0].group(3) is not None:
        return content
    at = found[0].end(2)
    return block[:at] + DIGEST_FIELD + _digest(normalised(block)) + block[at:] + team


def untouched(kept: bytes) -> Optional[bool]:
    """Whether Cortex's block of ``kept`` is still what Cortex wrote, by the digest in its marker, whatever
    the team's part holds (ADR-012 §3.2): ``None`` when it carries none to tell — no marker, several, or one
    without a digest."""
    block = parts(LINE_BREAK.sub(b"\n", without_bom(kept)))[0]
    found = list(BOOTSTRAP_MARKER.finditer(block))
    if len(found) != 1 or found[0].group(3) is None:
        return None
    marker = found[0]
    plain = block[:marker.start(3) - len(DIGEST_FIELD)] + block[marker.end(3):]
    return _digest(normalised(plain)) == marker.group(3)


def with_team_part(written: bytes, kept: bytes) -> bytes:
    """``written``, a bootstrap as Cortex writes it, followed by the team's part of ``kept``, byte for byte
    (ADR-012 §3.3, §3.4): ``written`` as it is when ``kept`` has no end marker — and when ``written`` has
    none, a version before format 2, where the team's part would join Cortex's block: it stays in the
    ``.bak``. With one, the block takes ``kept``'s line endings and byte order mark, so that the file
    keeps one of each."""
    old, team = parts(without_bom(kept))
    if not END_MARKER.search(written) or (not team and not END_MARKER.search(old)):
        return written
    block = parts(written)[0]
    if b"\r\n" in old:
        block = block.replace(b"\n", b"\r\n")
    return (BOM if kept.startswith(BOM) else b"") + block + team


class Kept(NamedTuple):
    """What a kept instructions file is: a sentence about the file, the same after "kept:", and
    whether ``--force`` is the way out — not for a file a later Cortex wrote, which it would downgrade."""
    sentence: str
    phrase: str
    force: bool = True
    layout: bool = False    # written for the other layout, which --force without --workspace keeps (#329)


def kept_bootstrap(kept: bytes, template: bytes) -> Optional[Kept]:
    """What a kept instructions file is, against the template the pinned version writes for this
    layout: ``None`` for a bootstrap of that layout and format. The first rule that applies, in the
    order of ADR-010 §3.1. A version whose templates carry no marker keeps the checks it had."""
    kept = without_bom(kept)
    wanted, found = markers(template), markers(kept)
    if not bootstrap_of_cortex(kept) and not found:
        return TITLE_BELOW_TEXT if title_below_text(kept) else NO_BOOTSTRAP
    if not wanted:
        if not reads_the_local_file(kept):
            return Kept(f"is an older Cortex bootstrap: it does not read {config.LOCAL_FILE}, which names the spec now",
                        f"an older Cortex bootstrap, which does not read {config.LOCAL_FILE}")
        # The layout its title says — below blank lines and comments too — as cortex init --force reads it.
        writes_workspace = written_for_workspace(template)
        if written_for_workspace(kept) != writes_workspace:
            return _other_layout("workspace" if writes_workspace else "single")
        return None
    if len(found) > 1:
        return Kept("holds several bootstrap markers, so none is read", "it holds several bootstrap markers, so none is read")
    if not found:
        return Kept("is an older Cortex bootstrap, written before the bootstrap marker",
                    "an older Cortex bootstrap, written before the bootstrap marker")
    (layout, has), (writes_layout, writes) = found[0], wanted[0]
    if layout != writes_layout:
        return _other_layout(writes_layout)
    if has < writes:
        return Kept(f"is an older Cortex bootstrap: format {has}, where this version writes format {writes}",
                    f"an older Cortex bootstrap, of format {has} where this version writes format {writes}")
    if has > writes:
        return Kept(f"was written by a later Cortex: format {has}, where this version writes format {writes}",
                    f"a bootstrap of a later Cortex, of format {has} where this version writes format {writes}", force=False)
    return None


NO_BOOTSTRAP = Kept("holds no Cortex bootstrap: the tool will not find Cortex", "it holds no Cortex bootstrap")
# The templates' heading below a line of text: a team's notes quoting it, or a bootstrap of before the marker a team
# wrote a line above — which, Cortex cannot tell, nor the layout: --force writes the run's own.
TITLE_BELOW_TEXT = Kept("holds Cortex's title below a line of text, so Cortex cannot tell it for its bootstrap, nor "
                        "its layout; a workspace's takes --workspace",
                        "Cortex's title below a line of text, so neither its bootstrap nor its layout can be told")


def title_below_text(content: bytes) -> bool:
    """The templates' heading in Cortex's block of ``content``, below a line of text — no title (``title_line``)."""
    return title_line(content) is None and any(
        line.startswith(TITLE) for line in without_frontmatter(parts(normalised(content))[0]).split(b"\n"))


def forced(path: Path, template: bytes, personality: bool, current: Optional[bytes]) -> bytes:
    """What ``cortex init --force`` writes at ``path`` from ``template`` over ``current``, the file there or
    ``None``: the team's part stays (ADR-012 §3.3)."""
    wanted = stamped(for_tool(path, instructions(template, personality)))
    return wanted if current is None else with_team_part(wanted, current)


def _other_layout(wanted: str) -> Kept:
    other = "a workspace" if wanted == "single" else "a single project"
    return Kept(f"was written for {other}", f"a bootstrap written for {other}", layout=True)


def _refuse_a_file_on_the_way(path: Path, what: str) -> None:
    """A directory to create — ``path`` or one of its parents — that is a file already would stop
    the writes half-way: refused before any of them."""
    for directory in [*reversed(path.parents), path]:
        if os.path.lexists(directory) and not directory.is_dir():   # a file, or a link that leads nowhere
            raise InitError(f"{what}: {display(str(directory))} is a file, not a folder")


def _backup_of(path: Path, content: bytes) -> Tuple[Path, bool]:
    """Where ``--force`` keeps the file it replaces — ``FILE.bak``, or ``FILE.bak.N`` when a
    ``.bak`` of other content is there already: none is ever overwritten — and whether it is to be
    written, not when a backup of this content is there already."""
    for n in range(1000):
        backup = path.with_name(path.name + (".bak" if n == 0 else f".bak.{n}"))
        if not backup.exists():
            return backup, True
        if backup.is_file() and backup.read_bytes() == content:
            return backup, False
    raise InitError(f"--force: {display(str(path))} has a thousand backups — remove some")


def _tool(options: argparse.Namespace, root: Path) -> Optional[str]:
    """The tool whose instructions file to write: ``--tool``; for a new project, ``copilot``, the
    default of the script ``cortex init`` replaced, whatever file is there already — one written
    by hand holds no Cortex bootstrap; for a project that has a ``cortex.toml``, the tool whose
    file is there, so that a second ``cortex init`` writes no other. ``None`` when several are
    there, and none is named."""
    if options.tool is not None:
        return options.tool
    if not (root / config.PROJECT_FILE).is_file():
        return "copilot"
    found = [tool for tool, rel in TOOLS.items() if (root / rel).is_file()]
    if len(found) > 1:
        if options.force:
            raise InitError(f"--force: several instructions files are there — {', '.join(TOOLS[t] for t in found)}: "
                            "name the one to write with --tool")
        return None
    return found[0] if found else "copilot"


def _services(options: argparse.Namespace, root: Path, out: TextIO, err: TextIO) -> List[str]:
    """The workspace's services, checked: each a folder inside it, none an existing file."""
    names = list(options.service)
    if options.workspace and not names:
        if console.stdin_is_terminal():
            names = prompt_services(out)
        else:
            err.write("note: no service created — --service NAME adds one; names are asked for on a terminal only\n")
    for name in names:
        _refuse_a_file_on_the_way(root / service_path(name), f"--service {name}")
    return [service_path(name) for name in names]


def init(options: argparse.Namespace, cwd: str, out: TextIO, err: TextIO) -> None:
    root = Path(display(os.path.normpath(os.path.join(cwd, options.dir) if options.dir else cwd)))
    personality = not options.no_personality
    theme = options.theme if personality else "none"
    mode = "link" if options.link else "copy" if options.copy else None

    # Everything is checked before anything of the project is written: its instructions file,
    # its services, its theme, its cortex.toml, what is at cortex/ — and that no file stands where
    # a directory is to be made.
    _refuse_a_file_on_the_way(root, "the project root")
    tool_named = options.tool is not None
    known_before = [name for name, rel in TOOLS.items() if (root / rel).is_file()]
    tool = _tool(options, root)
    options.tool = tool
    if tool == "custom":
        if not options.instructions_file:
            raise InitError("--tool custom needs --instructions-file PATH")
        instructions_file = Path(os.path.join(cwd, options.instructions_file))
        _refuse_instructions_file(root, instructions_file)
    else:
        if options.instructions_file:
            raise InitError("--instructions-file goes with --tool custom")
        instructions_file = root / TOOLS[tool] if tool else None
    if instructions_file is not None:
        _refuse_a_file_on_the_way(instructions_file.parent, f"the instructions file {display(str(instructions_file))}")
    if theme is not None and not is_theme(theme):
        raise InitError(f'--theme {theme}: no theme name — letters, digits, ".", "_" and "-"')
    services = _services(options, root, out, err) if options.workspace else []

    the_store = store.Store()
    existing_project = (root / config.PROJECT_FILE).is_file()
    old = config.too_new(str(root), the_store.own) if existing_project and not options.source else None
    if old is not None:
        raise InitError(old)                # before any key is checked (ADR-013 §3.8)
    try:
        project = config.load(str(root)) if existing_project else None
    except config.ConfigError as error:
        raise InitError(f"{error}\ncortex init keeps an existing {config.PROJECT_FILE}: fix it, or remove it to "
                        "start over")
    # As the sync's own check (#310, #325): a link sync from a checkout replaces a link that leads nowhere.
    links = (mode or (project.sync if project else None)) == "link"
    sync.existing_entry(root / sync.LINK, the_store, project or _blank(root), replaces=options.source is not None and links,
                        pinned=project is not None)
    # A project keeps the version it pins — never another, older or newer; a new one takes this
    # binary's. A version newer than this binary is refused here.
    version = project.version if project else the_store.own
    if options.source:
        # A contributor's checkout (§3.5, --from): its templates and its spec, no version checked.
        spec = Path(os.path.abspath(options.source))
        if not sync.is_checkout(spec):
            raise InitError(f"--from {display(options.source)}: no Cortex checkout there — it holds no agents/, "
                            "templates/ and docs/")
        described = f"the checkout at {display(str(spec))}"
    else:
        the_store.ensure(version)
        spec = the_store.path(version)
        described = f"Cortex {version}"
    if project is None and theme is None:
        theme = "h2g2"
    # A theme Cortex ships, or one of the project's own, in its agents/personalities/.
    own_themes = root / "agents" / "personalities"
    if theme not in (None, "none") and not any((base / theme).is_dir()
                                               for base in (spec / "agents" / "personalities", own_themes)):
        themes = sorted({p.name for base in (spec / "agents" / "personalities", own_themes) if base.is_dir()
                         for p in base.iterdir() if p.is_dir()})
        raise InitError(f"theme '{theme}' is neither in {described} nor in agents/personalities/ — "
                        f"the themes are: {', '.join(themes)}")

    # Claude Code asks before it reads the store (ADR-008 §9): on a terminal, offer the team setting
    # to a new project.
    # A copy is inside the project: Claude Code reads it without asking, and there is nothing to offer.
    spec_mode = mode or (project.sync if project else None)
    claude_access = options.claude_access
    if claude_access is None and options.tool == "claude" and project is None and spec_mode != "copy" \
            and console.stdin_is_terminal():
        claude_access = ask(out, "Let Claude Code read the Cortex spec without asking for permission — "
                                 "claude_access = true in cortex.toml? [y/N] ")

    # cortex.toml: a new one, or only the keys the options name.
    if project is None:
        values = {"version": str(version), "theme": theme, **({"sync": mode} if mode else {}),
                  **({"claude_access": True} if claude_access else {})}
    else:
        values = {**({"theme": theme} if theme is not None and theme != project.theme else {}),
                  **({"sync": mode} if mode and mode != project.sync else {}),
                  **({"claude_access": claude_access}
                     if claude_access is not None and claude_access != project.claude_access else {})}
    project_text = config.render_project(str(root), values) if values else None
    personality = (theme if theme is not None else project.theme) != "none"
    if not options.source:
        # cortex.lock, read as sync reads it: a spec it does not name, or a file that is no lock,
        # stops init here, before its first write — as sync stops before its own.
        sync.check_lock(str(root), version, the_store, out)

    # Now the writes.
    root.mkdir(parents=True, exist_ok=True)
    if project_text is not None:
        config.write_text(str(root / config.PROJECT_FILE), project_text)
    if project is None:
        out.write(f"✓ {config.PROJECT_FILE}: Cortex {version}, theme {theme}\n")
    elif values:
        shown = ", ".join(f"{key} = {config.toml_value(value)}" for key, value in values.items())
        out.write(f"✓ {config.PROJECT_FILE}: {shown} — the rest kept, at Cortex {version}\n")
    else:
        out.write(f"✓ {config.PROJECT_FILE} kept, at Cortex {version}\n")

    # The spec, where the project finds it — then what git must ignore, asked of git once
    # cortex/ has its final form: a copy turned into a link is a file to git.
    sync.sync(str(root), mode, options.source, out, err, notes=False, registers=False)
    spec_mode = spec_mode or "store"
    entries = {config.LOCAL_FILE: config.LOCAL_FILE}
    if spec_mode in ("link", "copy"):
        # A link is a file to git: `cortex/` would not match it. `/cortex` matches the link and the copy;
        # `/.cortex-sync-*`, what a sync killed half-way leaves beside it — a link to this machine's store.
        entries[f"/{sync.LINK}"] = sync.LINK
        entries[f"/{sync.STAGING}*"] = f"{sync.STAGING}x"
    ignored = sync.add_to_gitignore(root, entries)
    if ignored:
        out.write(f"✓ .gitignore: {', '.join(ignored)}\n")
    if sync.in_no_repository(root):
        gitignore = ("its .gitignore keeps nothing out of a commit" if (root / ".gitignore").is_file()
                     else "no .gitignore is written")
        err.write(f"note: {display(str(root))} is in no git repository: {config.PROJECT_FILE} is committed nowhere, "
                  f"and {gitignore}. Where the workspace root is no repository (ADR-006, 2.B), each developer runs "
                  "cortex init in their own, and pins the version of their own cortex (ADR-008 §9).\n")
    else:
        sync._notes(root, spec_mode, err)

    templates = spec / "templates"
    # Without --workspace, a workspace's instructions file stays a workspace's (#329): no option names the other
    # layout, and the marker says the one the file was written for, as `cortex upgrade` reads it.
    kept_workspace = not options.workspace and instructions_file is not None and instructions_file.is_file() \
        and written_for_workspace(instructions_file.read_bytes())
    workspace = options.workspace or kept_workspace
    bootstrap = templates / ("bootstrap-instructions-workspace.md" if workspace else "bootstrap-instructions.md")
    template = bootstrap.read_bytes()
    if instructions_file is None:
        out.write("✓ the instructions files are kept — several are there; --tool names the one to write\n")
    elif instructions_file.exists() and not options.force:
        out.write(f"✓ {display(str(instructions_file))} kept — --force replaces it\n")
        kept = instructions_file.read_bytes()
        found = kept_bootstrap(kept, template)
        # A workspace's, the option chose this run's: --force alone keeps the file's layout (#329) — by its markers,
        # or its title before the marker — and would write a single project's again, so the command names the option.
        option = " --workspace" if options.workspace and not written_for_workspace(kept) else ""
        again = f"cortex init {tool_options(instructions_file, tool)}{option} --force writes it again, and keeps " \
            "it as .bak"
        if found is not None:
            err.write(f"note: {display(str(instructions_file))} {found.sentence} — "
                      f"{again if found.force else 'it is kept as it is'}\n")
        elif (PERSONALITY_BEGIN in parts(without_bom(kept))[0]) != personality:      # the team's part is its text
            err.write(f"note: {display(str(instructions_file))} was written {'without' if personality else 'with'} "
                      f"the personality block this theme needs removed or added — {again}\n")
        elif is_cursor_rule(instructions_file) and not applies_always(kept):
            err.write(f"note: {display(str(instructions_file))} has no `alwaysApply: true` line in a frontmatter, "
                      f"as Cursor's documentation writes it: Cursor may not apply it in every conversation — {again}\n")
    else:
        replaced = instructions_file.exists()
        current = instructions_file.read_bytes() if replaced else None
        wanted = forced(instructions_file, template, personality, current)
        if current == wanted:
            out.write(f"✓ {display(str(instructions_file))} is what --force would write: left as it is\n")
        else:
            instructions_file.parent.mkdir(parents=True, exist_ok=True)
            if replaced:
                backup, to_write = _backup_of(instructions_file, current)
                if not to_write:
                    out.write(f"✓ {display(str(instructions_file))}: a backup of it is there already\n")
                else:
                    os.replace(instructions_file, backup)
                    # Said when git says so: a team's .gitignore may hold *.bak, and a root in no repository has no git.
                    left = sync.git_ignores(root, os.path.relpath(backup, root)) is False
                    out.write(f"✓ {display(str(backup))}: the file --force replaces, as it was — "
                              f"{'git does not ignore it: ' if left else ''}carry over what you wrote in it, "
                              "then delete it\n")
            instructions_file.write_bytes(wanted)
            out.write(f"✓ {display(str(instructions_file))} {'replaced' if replaced else 'written'}\n")
            if kept_workspace:
                err.write(f"note: {display(str(instructions_file))} was written for a workspace, and is written for one "
                          "again — to write a single project's, remove it and run "
                          f"`cortex init {tool_options(instructions_file, tool)}`, then copy the team's part back from "
                          f"{display(str(backup))}, below the end marker\n")
    if not tool_named:
        # Found, not written: a file of another tool's, which a new project's default leaves alone.
        for name, rel in TOOLS.items():
            other = root / rel
            if other == instructions_file or not other.is_file():
                continue
            content = other.read_bytes()
            found = kept_bootstrap(content, template)
            if found is None:
                continue
            if not found.force:
                again = "kept as it is"
            elif found.layout and not workspace and content == forced(
                    other, (templates / "bootstrap-instructions-workspace.md").read_bytes(), personality, content):
                # A workspace's file as --force, which keeps its layout (#329), would write it: no command to name.
                err.write(f"note: {rel} is there and was kept: {found.phrase}, up to date as one\n")
                continue
            elif found.layout:
                # Never "remove it": at a root in no repository, its team's part would be lost with it (#329).
                again = f"cortex init --tool {name} --workspace --force writes a workspace's" if workspace else \
                    f"cortex init --tool {name} --force writes it again, a workspace's as it was written"
                again += ", and keeps it as .bak"
            else:
                again = f"cortex init --tool {name} --force replaces it, and keeps it as .bak"
            err.write(f"note: {rel} is there and was kept: {found.phrase} — {again}\n")
        if project is not None and not known_before and instructions_file is not None:
            err.write(f"note: no instructions file of a known tool was here, and {TOOLS['copilot']} is written: "
                      "a project of --tool custom names it again, with --instructions-file\n")

    overview = (templates / "project-overview.md.template").read_bytes()
    context = (templates / "project-context.md.template").read_bytes()
    for name, content in (("project-overview.md", overview), ("project-context.md", context)):
        if not (root / name).exists():
            (root / name).write_bytes(content)
            out.write(f"✓ {name} written — fill it in\n")

    if options.workspace:                   # the services and the team's pair: the option's, never the file's
        for name in services:
            folder = root / name
            folder.mkdir(parents=True, exist_ok=True)
            alias = PurePosixPath(name).name
            for file, content in (("project-overview.md", overview), ("project-context.md", context)):
                if not (folder / file).exists():
                    (folder / file).write_bytes(with_alias(content, alias))
                    out.write(f"✓ {name}/{file} — @{alias}\n")
        agents = root / "agents"
        if is_git_working_tree(agents):
            for file, content in (("project-overview.md", overview), ("project-context.md", context)):
                if not (agents / file).exists():
                    (agents / file).write_bytes(content)
                    out.write(f"✓ agents/{file} — the team's, agents/ is its own git repository (ADR-006)\n")
    sync.register(str(root), out, err)
    if options.tool == "claude" and claude_access is None and spec_mode != "copy" \
            and not config.load(str(root)).active_claude_access:
        out.write("\ntip: Claude Code asks before it reads the spec in the store. `claude_access = true` in "
                  "cortex.toml lets it read without asking — for the team; `cortex sync --claude-access`, for you.\n")
    out.write(f"\nCortex {version} is ready in {display(str(root))}.\n")


def _blank(root: Path) -> config.Project:
    """A project without its ``cortex.toml``: what sync made at cortex/ is judged by the ``spec`` its
    ``cortex.local.toml`` still names, as `cortex sync` judges it (#328) — nothing, when it names none."""
    return config.Project(root=str(root), version=store.Store().own, theme="none", sync=None,
                          local_theme=None, spec=config.local_spec(str(root)))


def run(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex init",
        description="Make a directory a Cortex project: its cortex.toml, the AI tool's instructions file, "
                    "project-overview.md and project-context.md (ADR-008 §3.7).")
    parser.add_argument("dir", nargs="?", metavar="DIR", help="the project's root (default: the current directory)")
    parser.add_argument("--theme", help="the team's personality theme (default: h2g2, or the one cortex.toml names)")
    parser.add_argument("--no-personality", action="store_true", help="no personality layer — wins over --theme")
    parser.add_argument("--workspace", action="store_true", help="multi-service workspace mode")
    parser.add_argument("--service", action="append", default=[], metavar="NAME",
                        help="a service of the workspace, a folder — repeatable; asked for when omitted on a terminal")
    parser.add_argument("--tool", choices=[*TOOLS, "custom"],
                        help="the AI tool: copilot (the default for a new project), cursor, claude, agents, custom — "
                             "for a project that has one, the tool whose instructions file is there")
    parser.add_argument("--instructions-file", metavar="PATH", help="the instructions file of --tool custom")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--link", action="store_true", help="link cortex/ to the spec: cortex.toml's sync = \"link\"")
    modes.add_argument("--copy", action="store_true", help="copy the spec into cortex/: cortex.toml's sync = \"copy\"")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing instructions file, kept as FILE.bak (FILE.bak.N when a .bak is "
                             "there) — cortex.toml changes only "
                             "for the options given")
    parser.add_argument("--from", dest="source", metavar="PATH",
                        help="take the templates and the spec from a checkout of Cortex, as `cortex sync --from` does")
    access = parser.add_mutually_exclusive_group()
    access.add_argument("--claude-access", dest="claude_access", action="store_const", const=True,
                        help="let Claude Code read the spec without asking: claude_access = true in cortex.toml — "
                             "asked on a terminal with --tool claude")
    access.add_argument("--no-claude-access", dest="claude_access", action="store_const", const=False,
                        help="do not ask, and leave it off")
    options = parser.parse_args(args)
    if options.service and not options.workspace:
        parser.error("--service goes with --workspace")
    try:
        init(options, working_directory(), sys.stdout, sys.stderr)
    except (InitError, config.ConfigError, store.StoreError, sync.SyncError) as error:
        return sync.failed("cortex init", str(error))
    except OSError as error:
        return sync.failed("cortex init", sync.os_error(error))
    except subprocess.CalledProcessError as error:
        return sync.failed("cortex init", f"{' '.join(map(str, error.cmd))} failed: "
                                          f"{(error.stderr or b'').decode(errors='replace').strip() or error.returncode}")
    return 0
