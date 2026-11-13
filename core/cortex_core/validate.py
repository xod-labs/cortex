"""Overlay validation — ADR-001 Tier 1 and Tier 2 (ADR-007).

``cortex validate`` runs this module (ADR-008 §3.8). It began as a literal port of the Bash
validator Cortex shipped until 0.9.0: same checks in the same order, same messages, same exit
codes, byte-identical output — so that nothing a host project relied on changed with the port.

It then departed from that output on purpose (ADR-007 §3.6 and its amendments): a file without a
header at the path of a base is reported as ``MISSING_HEADER``; a header key that is absent — not
only empty — is reported as ``MISSING_FIELD`` instead of aborting the run; paths are taken from
the root being scanned, where the script cut absolute paths at their first ``/agents/``; and what
was read is printed as it was read, where the script passed it through ``echo -e``.

The cascade's own rules — which layer replaces, which file cannot be overridden — are not
restated here: they come from the resolver, the one implementation the runtime runs too.
"""

# The standard library and this package only: the core runs from source, with nothing installed
# (ADR-007 §3.3), and the cortex binary embeds nothing else (ADR-008 §3.1).
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, TextIO, Tuple

from . import catalog, resolver
from .workspace import _refused, find, listed_services, same_directory, services  # noqa: F401 — find, services: this module's API

LAYERS = ("roles", "capabilities", "personalities", "workflows")

# {prog}: the command the caller runs — `cortex validate` (ADR-008 §3.8).
USAGE = 'Usage: {prog} [OPTIONS]\n\nOptions:\n  --service PATH     Validate overlays under a specific service folder only\n                     (path relative to project root or absolute)\n  --strict           Treat warnings as errors (CI-friendly)\n  -h, --help         Show this help\n\nExit codes:\n  0   No errors (and no warnings in --strict mode)\n  1   Errors detected (or warnings in --strict mode)\n  2   Bad arguments\n\nReference: ADR-001-layered-overrides.md\n'
SEPARATOR = '──────────────────────────────────────────'

_SPACE = "[ \t\n\v\f\r]"          # POSIX [[:space:]]
_ADDITIVE_TAG = re.compile(r"^##.*\(additive\)|^## 🚫 Disabled rules from base")
# C0, DEL and C1 — and C1 as the raw byte of a name that is not UTF-8, which surrogateescape
# decodes to U+DC80..U+DC9F
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\udc80-\udc9f]")


class Colors:
    def __init__(self, enabled: bool):
        on = enabled
        self.GREEN = "\x1b[0;32m" if on else ""
        self.RED = "\x1b[0;31m" if on else ""
        self.YELLOW = "\x1b[1;33m" if on else ""
        self.BLUE = "\x1b[0;34m" if on else ""
        self.BOLD = "\x1b[1m" if on else ""
        self.NC = "\x1b[0m" if on else ""


def shown(value: str) -> str:
    """``value`` with its control characters written out — ``\\x1b`` for ESC — so that nothing
    read from a project reaches the terminal as a control sequence (#85)."""
    return _CONTROL.sub(lambda m: "\\x%02x" % (ord(m.group()) & 0xFF), value)


def display(path: str) -> str:
    """``path`` as the report prints it: with ``/`` on every platform (ADR-008 §3.1). Only where
    the separator is another character — ``\\`` on Windows — is it rewritten: there, it cannot be
    part of a name, and on POSIX a ``\\`` is one."""
    return path.replace(os.sep, "/") if os.sep != "/" else path


def strip_prefix(value: str, prefix: str) -> str:
    """Bash ``${value#prefix}`` for a literal prefix."""
    return value[len(prefix):] if value.startswith(prefix) else value


def _stderr(text: str) -> None:
    """``text`` on stderr as the script wrote it: a name that is not UTF-8 as its own bytes."""
    buffer = getattr(sys.stderr, "buffer", None)
    if buffer is None:
        sys.stderr.write(text)
        return
    sys.stderr.flush()
    buffer.write(text.encode("utf-8", "surrogateescape"))
    buffer.flush()


def read_text(path: str) -> str:
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", "surrogateescape")


def extract_field(text: str, name: str) -> Optional[str]:
    """The script's ``sed -n '/<!-- OVERLAY/,/-->/p' | grep -E '^[[:space:]]*Name:' | head -1``.

    Returns ``None`` when no line matches — the key is absent.
    """
    block: List[str] = []
    in_range = False
    for line in text.split("\n"):
        if in_range:
            block.append(line)
            if "-->" in line:
                in_range = False
        elif "<!-- OVERLAY" in line:
            block.append(line)          # sed does not test the end address on the start line
            in_range = True
    key = re.compile(f"^{_SPACE}*{re.escape(name)}:")
    for line in block:
        if key.match(line):
            value = re.sub(f"^{_SPACE}*{re.escape(name)}:{_SPACE}*", "", line, count=1)
            return re.sub(f"{_SPACE}*$", "", value)
    return None


class Report:
    """Verdict lines and counters, as the script's ``report_*`` helpers print them.

    Paths and messages go through ``shown``: they carry what was read from the project. Paths
    also go through ``display``: the same on every platform.
    """

    def __init__(self, out: TextIO, colors: Colors):
        self.out, self.c = out, colors
        self.errors = self.warnings = self.checked = 0

    def echo(self, text: str) -> None:
        self.out.write(text + "\n")

    def error(self, rel_path: str, code: str, message: str) -> None:
        self.echo(f"{self.c.RED}✗{self.c.NC} {shown(display(rel_path))}")
        self.echo(f"  {self.c.RED}{code}{self.c.NC} — {shown(message)}")
        self.errors += 1

    def warning(self, rel_path: str, code: str, message: str) -> None:
        self.echo(f"{self.c.YELLOW}⚠{self.c.NC} {shown(display(rel_path))}")
        self.echo(f"  {self.c.YELLOW}{code}{self.c.NC} — {shown(message)}")
        self.warnings += 1

    def ok(self, rel_path: str) -> None:
        self.echo(f"{self.c.GREEN}✓{self.c.NC} {shown(display(rel_path))}")

    def info(self, rel_path: str, note: str) -> None:
        self.echo(f"{self.c.BLUE}ℹ{self.c.NC} {shown(display(rel_path))} ({note})")


def base_file(base: str, project_root: str, base_root: str) -> str:
    """Where a ``Base:`` header points. The logical ``cortex/agents/…`` identifier resolves
    against ``base_root`` (ADR-007 §3.4); anything else stays relative to the project, as in
    the script's ``"$PROJECT_DIR/$base"``."""
    if base.startswith("cortex/agents/"):
        return f"{base_root}/agents/{base[len('cortex/agents/'):]}"
    return f"{project_root}/{base}"


_NON_OVERRIDABLE = "characters.md is not overridable; fork the theme instead (see docs/creating-a-theme.md)"


def layer_directory(root: str, project_root: str, team_folder: Optional[str] = None) -> str:
    """Where the overlays of ``root`` are: the team's folder for the workspace — ``{project_root}/agents``
    unless ``cortex.toml`` renames it (ADR-013 §3.6) — and ``{service}/agents`` for a service."""
    if team_folder is not None and os.path.normpath(root) == os.path.normpath(project_root):
        return str(team_folder)
    return f"{root}/agents"             # as 1.7.0 wrote it: `--service .` reads ./agents


def check_overlay(file: str, root: str, project_root: str, base_root: str, report: Report,
                  team_folder: Optional[str] = None) -> None:
    """Validate one overlay file, found under ``root``'s layer directory — the script's
    ``validate_overlay_file``: ``{root}/agents/``, or the team's folder ``team_folder`` for the workspace.

    Its path under that directory and whether it is a workspace or a service overlay come from
    ``root``, never from the absolute path: where the project sits on disk changes nothing (#84).
    """
    rel_path = strip_prefix(file, f"{project_root}/")
    layers = layer_directory(root, project_root, team_folder)
    file_rel_to_agents = strip_prefix(file, f"{layers}/")
    in_workspace = os.path.normpath(root) == os.path.normpath(project_root)
    # A renamed team's folder is named as cortex.toml names it; otherwise "agents", as 1.7.0 said — `--service .` too.
    shown_layers = strip_prefix(layers, f"{project_root}/") if in_workspace and team_folder is not None else "agents"
    shown_team = strip_prefix(str(team_folder) if team_folder is not None else f"{project_root}/agents", f"{project_root}/")
    # The file's layer, and its path within it, as the resolver sees them
    layer, _, file_in_layer = file_rel_to_agents.partition("/")
    rule = resolver.semantic_for(layer, file_in_layer)
    report.checked += 1
    try:
        text = read_text(file)
    except OSError as error:
        # The script's head failed, said so on stderr, and so found no header.
        _stderr(f"head: cannot open '{display(file)}' for reading: {error.strerror}\n")
        text = ""

    # Tier 1.1 — header presence, in the first ten lines. Without one, a file at the path of a
    # base shadows it — the resolver stacks it, so it is an overlay missing its header
    # (ADR-007 §3.6), unless it is one the resolver never stacks: characters.md is the same
    # error with a header or without. A README is documentation, which the cascade never reads.
    # Anywhere else it is a custom addition.
    if not any("<!-- OVERLAY" in line for line in text.split("\n")[:10]):
        if catalog.is_readme(os.path.basename(file)):
            report.info(rel_path, "documentation — a README is never an overlay, skipping overlay checks")
            return
        if os.path.isfile(f"{base_root}/agents/{file_rel_to_agents}"):
            if rule is resolver.MergeSemantic.NOT_OVERRIDABLE:
                report.error(rel_path, "NON_OVERRIDABLE", _NON_OVERRIDABLE)
                return
            report.warning(rel_path, "MISSING_HEADER",
                           f"no <!-- OVERLAY --> header in its first 10 lines, yet it shadows the base "
                           f"'cortex/agents/{file_rel_to_agents}' — add the header at the top, or rename the file "
                           "if it is not meant to extend that base")
            return
        report.info(rel_path, "custom addition — no cortex base, skipping overlay checks")
        return

    # Tier 1.2 — required fields, absent or empty alike
    fields = {name: extract_field(text, name) for name in ("Base", "Scope", "Semantic")}
    for name, value in fields.items():
        if not value:
            report.error(rel_path, "MISSING_FIELD", f"{name}: is required in OVERLAY header")
            return
    base, scope, semantic = fields["Base"], fields["Scope"], fields["Semantic"]

    # Tier 1.3 — base exists
    if not os.path.isfile(base_file(base, project_root, base_root)):
        report.error(rel_path, "BASE_NOT_FOUND", f"Base: '{base}' does not exist (typo, or upstream removed it?)")
        return

    # Tier 1.4 — semantic valid
    if semantic not in ("additive", "replacement"):
        report.error(rel_path, "INVALID_SEMANTIC", f"Semantic: must be 'additive' or 'replacement' (got '{semantic}')")
        return

    # Tier 1.5 — replacement only where the resolver replaces: workflows
    if semantic == "replacement" and rule is not resolver.MergeSemantic.REPLACEMENT:
        report.error(rel_path, "REPLACEMENT_OUTSIDE_WORKFLOWS",
                     f"Semantic: replacement is only allowed for files under "
                     f"{shown_team if in_workspace and team_folder is not None else 'agents'}/workflows/")
        return

    # Tier 1.6 — path mirroring
    base_rel_to_agents = strip_prefix(base, "cortex/agents/")
    if file_rel_to_agents != base_rel_to_agents:
        report.error(rel_path, "PATH_MIRROR",
                     f"overlay path '{shown_layers}/{file_rel_to_agents}' must mirror base "
                     f"'cortex/agents/{base_rel_to_agents}'")
        return

    # Tier 2.1 — non-overridable, as the resolver says: characters.md (reported as an error,
    # as the script does). Past the mirror check, the base and the file share this path.
    if rule is resolver.MergeSemantic.NOT_OVERRIDABLE:
        report.error(rel_path, "NON_OVERRIDABLE", _NON_OVERRIDABLE)
        return

    # Tier 2.2 — the file is in a known layer: holds by construction, since discovery walks the
    # four layer directories only

    # Tier 2.3 — scope vs location
    if scope.startswith("workspace") and not in_workspace:
        report.warning(rel_path, "SCOPE_MISMATCH",
                       f"Scope: '{scope}' but file is not at workspace root ({shown_team}/...)")
    if scope.startswith("service") and in_workspace:
        report.warning(rel_path, "SCOPE_MISMATCH",
                       f"Scope: '{scope}' but file is at workspace root — should be under {{service}}/agents/")

    # Tier 2.4 — an additive overlay tags at least one section
    if semantic == "additive" and not any(_ADDITIVE_TAG.search(line) for line in text.split("\n")):
        report.warning(rel_path, "SECTIONS_UNTAGGED",
                       "additive overlay should tag at least one section as '(additive)' or use '## 🚫 Disabled rules from base'")

    report.ok(rel_path)


# --------------------------------------------------------------------------- #
# Discovery — the script's two ``find`` calls
# --------------------------------------------------------------------------- #


def overlay_roots(project_root: str, base_root: str, service: str, team_folder: Optional[str] = None) -> List[str]:
    """The workspace, when it has its team's folder — ``agents/``, unless ``cortex.toml`` renames it
    (ADR-013 §3.6) — then every service that has both a ``project-overview.md`` and an ``agents/`` — or
    the one ``--service`` names. A service the runtime refuses is none: one whose ``agents/`` is the team's
    folder, a link to it, holds the team's overlays, which the workspace's root checks already (ADR-013 §3.6).

    A project root that is the base itself — Cortex validating itself — is no workspace tier:
    its files are the base, read once and never stacked onto themselves (ADR-007 §3.1).
    Services are looked for outside the base, wherever it is mounted and whatever it is called,
    and outside any directory named ``cortex`` or ``.git`` below the project root — a service
    may mount its own Cortex.
    """
    if service:
        return [_service_path(project_root, service)]
    is_base = same_directory(project_root, base_root) and team_folder is None
    roots = [project_root] if os.path.isdir(layer_directory(project_root, project_root, team_folder)) \
        and not is_base else []
    return roots + [s for s in listed_services(project_root, base_root, team_folder) if os.path.isdir(f"{s}/agents")]


def _service_path(project_root: str, service: str) -> str:
    return service if os.path.isabs(service) else f"{project_root}/{service}"


def refused_service(project_root: str, service: str, team_folder: Optional[str] = None) -> Optional[str]:
    """Why ``--service`` names a service the runtime refuses (ADR-013 §3.6, #353) — ``None`` when it does not, or
    names no folder below the project root: a service that is ``agents/`` or the team's folder, or in one, or whose own
    ``agents/`` is the team's folder, a link to it. ``overlay_roots`` leaves such a service out; named, it is warned of
    and not checked, since its overlays would be the team's, checked as a service's."""
    root = Path(project_root)
    team = Path(team_folder) if team_folder is not None else root / "agents"
    try:
        folder = os.path.relpath(_service_path(project_root, service), project_root)
    except ValueError:                  # another drive, on Windows
        return None
    if folder == os.curdir or folder.split(os.sep)[0] == os.pardir:
        return None
    folder = Path(folder).as_posix()
    if not _refused(root, folder, team):
        return None
    name = strip_prefix(team.as_posix(), f"{root.as_posix()}/")
    team_s = f"the team's folder, {name}/, whose overlays `cortex validate` checks as the workspace's"
    parts = Path(folder).parts
    for path in (root.joinpath(*parts[:depth]) for depth in range(1, len(parts) + 1)):
        if _same_file(path, team):
            return f"the runtime serves no service in {team_s} — run it without --service"
        if _same_file(path, root / "agents"):           # a team's folder renamed: agents/ is no service's either
            return f"the runtime serves no service in {path.relative_to(root).as_posix()}/, nor reads it"
    return f"its agents/ is {team_s} — run it without --service"


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def validate(project_root: str, base_root: str, service: str, strict: bool, out: TextIO, colors: Colors,
             project_warnings: Sequence[Tuple[str, str, str]] = (), team_folder: Optional[str] = None) -> int:
    """Validate every overlay under the overlay roots; return the script's exit code.

    ``project_warnings`` — ``(file, code, message)`` — are what the caller found of the project
    itself, its ``cortex.lock`` (ADR-010 §3.4): reported first, and counted as warnings.
    ``team_folder`` is the workspace's layer directory, the team's folder (ADR-013 §3.6)."""
    c = colors
    report = Report(out, c)
    report.echo(f"{c.BOLD}{c.BLUE}Cortex overlay validator{c.NC}")
    report.echo(f"  Project root:  {shown(display(project_root))}")
    report.echo(f"  Cortex dir:    {shown(display(base_root))}")
    report.echo(f"  Strict mode:   {'true' if strict else 'false'}")
    if service:
        report.echo(f"  Service only:  {shown(display(service))}")
    report.echo("")

    if project_warnings:
        report.echo(f"{c.BOLD}── Project ──{c.NC}")
        for rel_path, code, message in project_warnings:
            report.warning(rel_path, code, message)
        report.echo("")

    # A warning, never an error: a check a later cortex adds fails `--strict` only (ADR-008 §9).
    refused = refused_service(project_root, service, team_folder) if service else None
    if refused is not None:
        report.echo(f"{c.BOLD}── Service ──{c.NC}")
        report.warning(service, "SERVICE_REFUSED", f"a service the runtime refuses — {refused}")
        report.echo("")
    roots = overlay_roots(project_root, base_root, service, team_folder) if refused is None else []
    if not roots:
        if refused is None:
            where = "no agents/ directory at workspace or service level" if team_folder is None else \
                f"no {strip_prefix(str(team_folder), f'{project_root}/')}/ directory at workspace level, no agents/ at service level"
            report.echo(f"{c.YELLOW}ℹ{c.NC}  No overlay roots found ({where}).")
            report.echo("   Nothing to validate. This is expected if you haven't created overlays yet.")
        if report.warnings:   # the project's own, or the service's: said in the summary, as an overlay's would be
            report.echo(SEPARATOR)
            report.echo(f"Warnings: {c.YELLOW}{c.BOLD}{report.warnings}{c.NC}")
            if strict:
                report.echo(f"{c.RED}Strict mode: warnings count as errors.{c.NC}")
                return 1
        return 0

    for root in roots:
        in_workspace = os.path.normpath(root) == os.path.normpath(project_root)
        rel_root = "." if in_workspace else strip_prefix(root, f"{project_root}/")
        report.echo(f"{c.BOLD}── Scope: {shown(display(rel_root))} ──{c.NC}")
        found = 0
        for layer in LAYERS:
            layer_dir = f"{layer_directory(root, project_root, team_folder)}/{layer}"
            if not os.path.isdir(layer_dir):
                continue
            for path in find(layer_dir, "*.md", regular_files=True, follow_links=True):
                check_overlay(path, root, project_root, base_root, report, team_folder)
                found += 1
        if found == 0:
            report.echo("  (no overlay files)")
        report.echo("")

    report.echo(SEPARATOR)
    report.echo(f"Checked:  {c.BOLD}{report.checked}{c.NC} files")
    report.echo(f"Errors:   {c.RED}{c.BOLD}{report.errors}{c.NC}" if report.errors else f"Errors:   {c.GREEN}0{c.NC}")
    report.echo(f"Warnings: {c.YELLOW}{c.BOLD}{report.warnings}{c.NC}" if report.warnings else f"Warnings: {c.GREEN}0{c.NC}")
    if report.errors:
        return 1
    if strict and report.warnings:
        report.echo(f"{c.RED}Strict mode: warnings count as errors.{c.NC}")
        return 1
    return 0


def _stream(stream: TextIO) -> TextIO:
    """Bytes out as the script writes them — file names that are not valid UTF-8 included."""
    return io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="surrogateescape", newline="\n", write_through=True)


def main(argv: List[str], *, project_root: str, base_root: str, prog: str = "cortex validate",
         project_warnings: Sequence[Tuple[str, str, str]] = (), team_folder: Optional[str] = None) -> int:
    """``cortex validate [--service PATH] [--strict] [-h|--help]``.

    The two roots are no options: the ``cortex`` command finds them from the project (ADR-008
    §3.8), and the team's folder from its ``cortex.toml`` (ADR-013 §3.6). ``prog`` names the command
    in the help.
    """
    args = list(argv)
    # What the caller already wrote goes out first: the wrappers write under its buffers.
    sys.stdout.flush()
    sys.stderr.flush()
    out, err = _stream(sys.stdout), _stream(sys.stderr)
    try:
        return _main(args, project_root, base_root, out, err, prog, project_warnings, team_folder)
    finally:
        # The wrappers borrow the process's own streams: detached, returning leaves stdout and
        # stderr open for whatever runs next in this process.
        out.detach()
        err.detach()


def _main(args: List[str], project_root: str, base_root: str, out: TextIO, err: TextIO,
          prog: str, project_warnings: Sequence[Tuple[str, str, str]] = (), team_folder: Optional[str] = None) -> int:
    service, strict = "", False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--service":
            if i + 1 >= len(args):
                return 1          # the Bash validator's `shift 2` failed under errexit: exit 1, nothing printed
            service, i = args[i + 1], i + 2
        elif arg == "--strict":
            strict, i = True, i + 1
        elif arg in ("-h", "--help"):
            out.write(USAGE.format(prog=prog))
            return 0
        else:
            err.write(f"Unknown argument: {arg}\n")
            err.write(USAGE.format(prog=prog))
            return 2
    return validate(project_root, base_root, service, strict, out, Colors(out.isatty()), project_warnings,
                    team_folder)
