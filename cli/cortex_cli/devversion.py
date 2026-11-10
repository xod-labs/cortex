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
  hold: a heading or an entry may be indented up to three spaces, a space or a tab after its ``###``,
  an entry starts with ``-``, ``*`` or ``+``, a fenced code block holds no section, heading nor entry,
  and a line ends at a line feed or a carriage return alone — not at the other breaks Python splits
  text at, which an entry may hold;
- a changelog that writes a pre-release's section — Cortex's does not (ADR-009 §3.7) — has it as
  a floor: above the base, ``3.1.0-rc.1`` makes the version the higher of its release and the
  bump's — ``3.1.0`` after a fix or an addition, ``4.0.0`` after a removal.

Once a release's section is written, the branch prepares the next one: the release job stamps
``X.Y.Z`` itself. ``cli/build.py --development-version N`` prints it for the CI's builds.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .semver import Version

UNRELEASED = "unreleased"
# A second-level section: `## [1.7.0] - 2026-11-06 — …`, `## [Unreleased]` — indented up to three spaces, as
# Markdown has a heading.
SECTION = re.compile(r" {0,3}## \[(?P<label>[^\]]+)\].*")
KIND = re.compile(r" {0,3}###[ \t]+(?P<kind>.+?)\s*")
# The kinds of Keep a Changelog, as a heading names one — its first word, after an emoji or a mark: `### Added`,
# `### ✨ Added`, `### fixed (CLI)`; `### Notes: nothing removed` names none.
KINDS = re.compile(r"^[\W_]*(added|changed|deprecated|removed|fixed|security)\b", re.IGNORECASE)
# An entry is a line of its own starting a list item, indented up to three spaces; what follows it, indented
# further, is its text.
ENTRY = re.compile(r" {0,3}[-*+][ \t]+(?P<text>.*)")
# A code fence opens and closes a block that holds no Markdown: three backticks or tildes, or more, indented up to
# three spaces — closed by a line of the same character, at least as many.
FENCE = re.compile(r" {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)")
BREAKING = re.compile(r"(?:\*\*)?💥")
# A heading marked breaking, `### 💥 Breaking`, `### Breaking changes`: its entries are, whatever kind it names.
# The word first, or the mark: `### Fixed (no breaking change)` and `### Non-breaking` are not.
BREAKING_KIND = re.compile(r"💥|^\W*breaking\b", re.IGNORECASE)
MAJOR = frozenset({"Removed", "Breaking"})
MINOR = frozenset({"Added", "Changed", "Deprecated"})


def released(text: str) -> List[Version]:
    """The version of every section the changelog holds, ``[Unreleased]`` and any label that is
    no version aside, in the file's order."""
    found = []
    for line in _lines(text):
        match = SECTION.fullmatch(line.rstrip())
        if match and Version.valid(match.group("label")):
            found.append(Version(match.group("label")))
    return found


def unreleased(text: str) -> Dict[str, List[str]]:
    """The entries of ``[Unreleased]`` by kind, ``{"Added": [...], "Fixed": [...]}``: each entry's
    first line, without its marker. A kind with no entry is left out; a heading marked breaking is the
    kind ``Breaking``; one that names none of Keep a Changelog's is a kind of its own, which asks
    nothing."""
    entries: Dict[str, List[str]] = {}
    inside, kind = False, None
    for line in _lines(text):
        line = line.rstrip()
        section = SECTION.fullmatch(line)
        if section:
            inside = section.group("label").strip().lower() == UNRELEASED
            continue
        if not inside:
            continue
        heading = KIND.fullmatch(line)
        if heading:
            named = KINDS.search(heading.group("kind"))
            kind = "Breaking" if BREAKING_KIND.search(heading.group("kind")) else \
                named.group(1).capitalize() if named else heading.group("kind")
            continue
        entry = ENTRY.fullmatch(line)
        if entry and kind is not None:
            entries.setdefault(kind, []).append(entry.group("text"))
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


def _lines(text: str) -> List[str]:
    """The lines Markdown reads as such, those of a fenced code block left out — an example of a changelog in one
    is none of its sections. Markdown's line endings alone: ``splitlines`` would also break at U+2028, U+0085 or a
    form feed in an entry."""
    kept: List[str] = []
    fence: Optional[str] = None
    for line in re.split(r"\r\n|\r|\n", text.removeprefix("\ufeff")):
        found = FENCE.fullmatch(line.rstrip())
        if fence is None and found and not (found.group("fence")[0] == "`" and "`" in found.group("info")):
            fence = found.group("fence")
        elif fence is not None:
            if found and found.group("fence")[0] == fence[0] and len(found.group("fence")) >= len(fence) \
                    and not found.group("info").strip():
                fence = None
        else:
            kept.append(line)
    return kept


def development(text: str, build: Optional[int] = None) -> str:
    """``X.Y.Z-dev`` for a source checkout, ``X.Y.Z-dev.N`` for the CI's build number ``N``."""
    version = f"{prepared(text)}-dev"
    if build is not None:
        if isinstance(build, bool) or not isinstance(build, int) or build < 0:
            raise ValueError(f"not a build number: {build!r}")
        version = f"{version}.{build}"
    return Version(version).text
