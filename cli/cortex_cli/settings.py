"""``cortex config`` — each setting of the project, its value and where it comes from (ADR-013 §3.9).

The files of the project, the machine's ``config.toml`` and the variables, resolved as every command
resolves them (``cortex_core.cascade``): a value, then its origin — a file as the project root names
it, a variable, or *default* — and what it won over. ``--locked`` gives the team's resolution, without
the developer's ``cortex.local.toml``: the version and the lock ``cortex sync --locked`` checks, and
what the runtime reads — ``cortex sync --locked`` still applies the developer's own ``theme`` and
``claude_access`` to their machine. Outside a project, the machine's keys only. It writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, TextIO

from cortex_core import cascade, project

from . import config, machine, net, store, sync
from .paths import display, working_directory
from .semver import Version

FORMAT = 1


class SettingsError(Exception):
    pass


def resolution(root: Optional[str], locked: bool = False) -> cascade.Resolution:
    """The settings of the project at ``root`` — the machine's alone without one. ``ConfigError``
    for a file refused, naming it; the machine's file only warns."""
    values, warnings, name = machine.read(store.cortex_home)
    if root is None:
        return cascade.resolve(None, None, values, os.environ, machine_name=name, warnings=warnings)
    folder, known = config.team_folder(root)        # cortex.toml's agents, before the other keys (ADR-013 §3.8)
    try:
        data, local = cascade.read_project(root, local=not locked, folder=folder)
        return cascade.resolve(data, local, values, os.environ, machine_name=name, locked=locked,
                               committed=known if known is not None else sync.committed(root), warnings=warnings,
                               team_data=cascade.read_team(root, folder), team_name=cascade.team_name(folder))
    except project.ProjectFileError as error:
        raise config.ConfigError(str(error))


def _shown(value: object) -> str:
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _origin(setting: cascade.Setting, newer: Optional[str], found: Optional[cascade.Resolution] = None) -> str:
    said = setting.origin
    if setting.key == "version" and found is not None and found.team_version is not None \
            and found.team_version.origin != setting.origin:
        said += f" — above the team's {found.team_version.value} ({found.team_version.origin})"
    if setting.locked:
        said += " — locked"
    if setting.key == "version" and newer is not None:
        said += f" — newer than this cortex, {newer}: {store.update_command(Version(str(setting.value)))}"
    return said


def _too_new(found: cascade.Resolution) -> Optional[str]:
    """This cortex's version, when the project's is newer — ``None`` otherwise."""
    own = store.own_version()
    if "version" in found and store.too_old_for(Version(str(found.value("version"))), own):
        return str(own)
    return None


def _refused_url(found: cascade.Resolution) -> Optional[str]:
    """Why a download would refuse ``releases_url`` — ``None`` when it would not. Said, never an
    error: ``cortex config`` prints every setting, and ``cortex config version`` is read in CI."""
    if "releases_url" not in found:
        return None
    try:
        net.check_url(str(found.value("releases_url")).rstrip("/"))
    except net.DownloadError as error:
        return f"{found['releases_url'].origin}: {error} — a download refuses it"
    return None


def _loosely(root: str, error: config.ConfigError) -> cascade.Resolution:
    """A project whose files this cortex refuses, pinning a version newer than it: its version,
    read before any key is checked (§3.8), and the machine's keys — what a later version's keys are,
    this cortex does not know. Any other refusal stands."""
    pinned = config.loose_pin(root)
    if pinned is None or not store.too_old_for(pinned[1], store.own_version()):
        raise error
    found = machine.resolution(store.cortex_home)
    version = cascade.Setting("version", str(pinned[1]), pinned[0])
    return cascade.Resolution({"version": version, **found.settings}, committed=sync.committed(root),
                              warnings=found.warnings)


def show(cwd: str, key: Optional[str], locked: bool, as_json: bool, out: TextIO) -> int:
    root = config.find_project(cwd)
    loose = False
    try:
        found = resolution(root, locked)
    except config.ConfigError as error:
        if root is None:
            raise
        found, loose = _loosely(root, error), True
    if key is not None and key not in found and loose and key in cascade.KEYS:
        # A key this cortex knows, of a project it cannot read: the reason is the version.
        raise config.ConfigError(str(config.too_new(root, store.own_version())))
    if key is not None and key not in found:
        known = project.listed(cascade.KEYS if loose else tuple(found.settings))
        raise SettingsError(f"no setting {json.dumps(key)} — {'cortex config knows ' if root else 'outside a project, cortex config knows '}"
                            f"{known}")
    newer = _too_new(found) if root is not None else None
    if not as_json:
        for note in found.notes:
            sys.stderr.write(f"note: {note}\n")
    warnings = list(found.warnings)
    refused = _refused_url(found)
    if refused is not None:
        warnings.append(refused)
        sys.stderr.write(f"warning: {refused}\n")
    if as_json:
        keys = [key] if key is not None else list(found.settings)
        out.write(json.dumps({
            "format": FORMAT,
            "project": display(root) if root is not None else None,
            "committed": found.committed,
            "locked": locked,
            "cortex": str(store.own_version()),
            "too_new": newer is not None,
            "warnings": warnings,
            "settings": {name: {"value": found[name].value, "origin": found[name].origin,
                                "overridden": [{"origin": origin, "value": value} for origin, value in found[name].overridden],
                                "locked": found[name].locked}
                         for name in keys},
            "team_version": ({"value": found.team_version.value, "origin": found.team_version.origin}
                             if found.team_version is not None else None),
            "notes": list(found.notes),
        }, indent=2) + "\n")
        return 0
    if key is not None:
        value = found.value(key)
        out.write(("" if value is None else _shown(value)) + "\n")
        if key == "version" and newer is not None:
            sys.stderr.write(f"note: Cortex {value} is newer than this cortex, {newer} — update it:\n"
                             f"    {store.update_command(Version(str(value)))}\n")
        return 0
    width = max(len(name) for name in found.settings) + 2
    values = {name: _shown(setting.value) for name, setting in found.settings.items()}
    column = max(len(text) for text in values.values()) + 4
    for name, setting in found.settings.items():
        out.write(f"{name:<{width}}{values[name]:<{column}}{_origin(setting, newer, found)}\n")
    return 0


def run(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex config",
        description="Print each setting of the project — the files, the machine's config.toml and the variables — "
                    "its value and where it comes from. Writes nothing. ADR-013 §3.9.")
    parser.add_argument("key", nargs="?", metavar="KEY", help="print this setting's value alone")
    parser.add_argument("--locked", action="store_true",
                        help="the team's resolution, without cortex.local.toml — the version `cortex sync --locked` "
                             "checks, and what the runtime reads")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON object: each setting's value, origin, what it overrode")
    options = parser.parse_args(args)
    try:
        return show(working_directory(), options.key, options.locked, options.json, sys.stdout)
    except SettingsError as error:
        return sync.failed("cortex config", str(error), 2)
    except config.ConfigError as error:
        return sync.failed("cortex config", str(error))
    except OSError as error:
        return sync.failed("cortex config", sync.os_error(error))
