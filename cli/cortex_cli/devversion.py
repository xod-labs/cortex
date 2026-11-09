"""The version a build of no release prepares, read from the changelog (ADR-009 §9, 2026-11-09).

A source checkout reports ``X.Y.Z-dev``; a build of no release, ``X.Y.Z-dev.N``, ``N`` the CI
run's number. ``X.Y.Z`` is the version the branch prepares, which ``CHANGELOG.md`` says:

- the base is the highest version among its sections, by semver order — not the first section
  after ``[Unreleased]``: a fix carried up from an older line, ``1.7.1`` written above ``3.0.0``,
  never lowers it. A changelog that names none starts from ``0.0.0``;
- a pre-release base, ``2.0.0-rc.1``, prepares its release, ``2.0.0``;
- otherwise ``[Unreleased]`` gives the bump: an entry under ``Removed``, or one marked breaking
  (``💥``), a major; one under ``Added``, ``Changed`` or ``Deprecated``, a minor; anything else —
  ``Fixed`` and ``Security`` only, or no entry yet — a patch.

Once a release's section is written, the branch prepares the next one: the release job stamps
``X.Y.Z`` itself. ``cli/build.py --development-version N`` prints it for the CI's builds.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .semver import Version

UNRELEASED = "Unreleased"
# A second-level section: `## [1.7.0] - 2026-11-06 — …`, `## [Unreleased]`.
SECTION = re.compile(r"## \[(?P<label>[^\]]+)\].*")
KIND = re.compile(r"### (?P<kind>.+?)\s*")
# An entry is a line of its own starting a list item; what follows it, indented, is its text.
ENTRY = re.compile(r"[-*] +(?P<text>.*)")
BREAKING = re.compile(r"(?:\*\*)?💥")
MAJOR = frozenset({"Removed"})
MINOR = frozenset({"Added", "Changed", "Deprecated"})


def released(text: str) -> List[Version]:
    """The version of every section the changelog holds, ``[Unreleased]`` and any label that is
    no version aside, in the file's order."""
    found = []
    for line in text.splitlines():
        match = SECTION.fullmatch(line.rstrip())
        if match and Version.valid(match.group("label")):
            found.append(Version(match.group("label")))
    return found


def unreleased(text: str) -> Dict[str, List[str]]:
    """The entries of ``[Unreleased]`` by kind, ``{"Added": [...], "Fixed": [...]}``: each entry's
    first line, without its marker. A kind with no entry is left out."""
    entries: Dict[str, List[str]] = {}
    inside, kind = False, None
    for line in text.splitlines():
        line = line.rstrip()
        section = SECTION.fullmatch(line)
        if section:
            inside = section.group("label") == UNRELEASED
            continue
        if not inside:
            continue
        heading = KIND.fullmatch(line)
        if heading:
            kind = heading.group("kind")
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
    base = max(versions) if versions else Version("0.0.0")
    major, minor, patch = base.core
    if base.pre:
        return Version(f"{major}.{minor}.{patch}")
    step = bump(unreleased(text))
    if step == "major":
        return Version(f"{major + 1}.0.0")
    if step == "minor":
        return Version(f"{major}.{minor + 1}.0")
    return Version(f"{major}.{minor}.{patch + 1}")


def development(text: str, build: Optional[int] = None) -> str:
    """``X.Y.Z-dev`` for a source checkout, ``X.Y.Z-dev.N`` for the CI's build number ``N``."""
    version = f"{prepared(text)}-dev"
    if build is not None:
        if isinstance(build, bool) or not isinstance(build, int) or build < 0:
            raise ValueError(f"not a build number: {build!r}")
        version = f"{version}.{build}"
    return Version(version).text
