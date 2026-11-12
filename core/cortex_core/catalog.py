"""The capability catalog — every capability card the cascade offers (ADR-007).

A union across the base, workspace and service capability directories, returned as
cascade-relative paths (``languages/php.md``). ``README.md`` files are not capabilities.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .resolver import _tiers


def is_readme(name: str) -> bool:
    """Whether a file name is a directory's README — documentation, in any case, never a card of
    the cascade."""
    return name.lower() == "readme.md"


def capability_dirs(root: Path, service: Optional[str] = None, *, base_root: Optional[Path] = None,
                    team_folder: Optional[Path] = None) -> List[Path]:
    """The capability directories of the cascade, base first — one per tier, as the resolver
    counts them; the workspace's in the team's folder (ADR-013 §3.6)."""
    return [tier / "capabilities" for tier in _tiers(root, service, base_root, team_folder)]


def capability_catalog(root: Path, service: Optional[str] = None, *, base_root: Optional[Path] = None,
                       team_folder: Optional[Path] = None) -> List[str]:
    """Cascade-relative paths of every capability available (e.g. ``languages/php.md``)."""
    found = set()
    for cap_dir in capability_dirs(root, service, base_root=base_root, team_folder=team_folder):
        if not cap_dir.is_dir():
            continue
        for md in cap_dir.rglob("*.md"):
            if is_readme(md.name):
                continue
            found.add("/".join(md.relative_to(cap_dir).parts))
    return sorted(found)
