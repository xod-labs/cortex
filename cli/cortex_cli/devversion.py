"""The version a build of no release prepares, read from the changelog (ADR-009 §9, 2026-11-09).

A source checkout reports ``X.Y.Z-dev``; a build of no release, ``X.Y.Z-dev.N``, ``N`` the CI
run's number. ``X.Y.Z`` is the version the branch prepares, which ``CHANGELOG.md`` says:

- the base is the highest release among its sections, by semver order — not the first section
  after ``[Unreleased]``: a fix carried up from an older line, ``1.7.1``, whose section sits among
  the others, never lowers it. A changelog that names none starts from ``0.0.0``;
- ``[Unreleased]`` gives the bump: an entry under ``Removed``, or one marked breaking (``💥``) — or
  under a heading so marked, ``### 💥 Breaking`` — a major; one under ``Added``, ``Changed`` or
  ``Deprecated``, a minor; anything else — ``Fixed`` and ``Security`` only, or no entry yet — a
  patch. A kind is read in any case, the heading's first word, an emoji before it or a note after it:
  ``### ✨ Added``, ``### Fixed (CLI)`` — ``### Notes: nothing removed`` names none. Markdown's rules
  hold, as ``markdown_lines`` reads them — the release actions read the changelog through the same
  file: a heading or an entry may be indented up to three spaces, a space or a tab after its ``###``,
  an empty ``###`` naming no kind;
  an entry is an item of the section's own list, started with ``-``, ``*`` or ``+``, its text on
  its line or below it — an item in an entry is its text; a fenced code block, an HTML comment or a
  block quote holds no section, heading nor entry — one opened in a list item ends with it; a line
  ends at a line feed or a carriage return alone; ``[Unreleased]`` is read in any case;
- a changelog that writes a pre-release's section — Cortex's does not (ADR-009 §3.7) — has it as
  a floor: above the base, ``3.1.0-rc.1`` makes the version the higher of its release and the
  bump's — ``3.1.0`` after a fix or an addition, ``4.0.0`` after a removal.

Once a release's section is written, the branch prepares the next one: the release job stamps
``X.Y.Z`` itself. ``cli/build.py --development-version N`` prints it for the CI's builds.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .markdown_lines import SECTION, is_unreleased, lines, sections
from .semver import Version

# Matched against a line without its trailing white space. An empty `###` is a heading too: it names no kind.
KIND = re.compile(r" {0,3}###(?:[ \t]+(?P<kind>\S.*))?")
# The kinds of Keep a Changelog, as a heading names one — its first word, after an emoji or a mark: `### Added`,
# `### ✨ Added`, `### fixed (CLI)`; `### Notes: nothing removed` names none.
KINDS = re.compile(r"^[\W_]*(added|changed|deprecated|removed|fixed|security)\b", re.IGNORECASE)
# An entry is a list item of the section's own list — none in another item, which is its text — marked `-`, `*` or
# `+`, indented up to three spaces — its text on its own line, or below it.
ENTRY = re.compile(r" {0,3}[-*+](?:[ \t]+(?P<text>.*))?")
BREAKING = re.compile(r"(?:\*\*)?💥")
# A heading marked breaking, `### 💥 Breaking`, `### Breaking changes`: its entries are, whatever kind it names.
# The word first, or the mark: `### Fixed (no breaking change)` and `### Non-breaking` are not.
BREAKING_KIND = re.compile(r"💥|^\W*breaking\b", re.IGNORECASE)
MAJOR = frozenset({"Removed", "Breaking"})
MINOR = frozenset({"Added", "Changed", "Deprecated"})


def released(text: str) -> List[Version]:
    """The version of every section the changelog holds, ``[Unreleased]`` and any label that is
    no version aside, in the file's order."""
    return [Version(label) for label, _ in sections(text) if Version.valid(label)]


def unreleased(text: str) -> Dict[str, List[str]]:
    """The entries of ``[Unreleased]`` by kind, ``{"Added": [...], "Fixed": [...]}``: each entry's
    first line, without its marker. A kind with no entry is left out; a heading marked breaking is the
    kind ``Breaking``; one that names none of Keep a Changelog's is a kind of its own, which asks
    nothing."""
    entries: Dict[str, List[str]] = {}
    inside, kind = False, None
    below: Optional[List[str]] = None       # the kind's entries, the last one's marker alone on its line
    for read in lines(text):
        if below is not None and read.nested and not read.item and not read.code and read.text.strip():
            below[-1] = read.text.strip()                       # its text, below it: the item's first line
        below = None
        if read.code:
            continue
        line = read.text.rstrip()
        section = SECTION.fullmatch(line)
        if section:
            inside = is_unreleased(section.group("label"))
            continue
        if not inside:
            continue
        heading = KIND.fullmatch(line)
        if heading:
            words = heading.group("kind") or ""
            named = KINDS.search(words)
            kind = "Breaking" if BREAKING_KIND.search(words) else named.group(1).capitalize() if named else words
            continue
        entry = ENTRY.fullmatch(line) if read.item and not read.nested else None
        if entry and kind is not None:
            entries.setdefault(kind, []).append(entry.group("text") or "")
            below = entries[kind] if not entry.group("text") else None
    return entries


def bump(entries: Dict[str, List[str]]) -> str:
    """``major``, ``minor`` or ``patch``: what the entries of ``[Unreleased]`` ask of the next version."""
    if MAJOR.intersection(entries) or any(BREAKING.match(entry) for found in entries.values() for entry in found):
        return "major"
    if MINOR.intersection(entries):
        return "minor"
    return "patch"


def prepared(text: str) -> Version:
    """The release the branch whose changelog is ``text`` prepares."""
    versions = released(text)
    releases = [version for version in versions if not version.pre]
    base = max(releases) if releases else Version("0.0.0")
    major, minor, patch = base.core
    step = bump(unreleased(text))
    bumped = Version(f"{major + 1}.0.0" if step == "major" else f"{major}.{minor + 1}.0" if step == "minor" else
                     f"{major}.{minor}.{patch + 1}")
    # A pre-release below the base is of a release at or below it: no floor above the bump.
    floors = [Version("{}.{}.{}".format(*version.core)) for version in versions if version.pre]
    return max([bumped, *floors])


def development(text: str, build: Optional[int] = None) -> str:
    """``X.Y.Z-dev`` for a source checkout, ``X.Y.Z-dev.N`` for the CI's build number ``N``."""
    version = f"{prepared(text)}-dev"
    if build is not None:
        if isinstance(build, bool) or not isinstance(build, int) or build < 0:
            raise ValueError(f"not a build number: {build!r}")
        version = f"{version}.{build}"
    return Version(version).text
