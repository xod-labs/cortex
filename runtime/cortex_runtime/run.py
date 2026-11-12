"""The /run contract, framework-agnostic — ADR-002 §3.2.

``resolve_run`` is the deterministic core (ADR-002 §8.1 pre-resolution): given a request
and a bound ``root`` (§3.4), it assembles everything the agentic loop (Phase 3) needs —
the system prompt (identity), the derived capabilities, and the advisory workflow recipe.

This module imports no web framework on purpose: the API layer (api.py) is a thin shell
over it, so the contract stays testable with zero install.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple, Union

from cortex_core.project import folded
from cortex_core.workspace import service_index

from .base import Pin, base_for, read_pin
from .context import derive_capabilities, read_project_context, read_project_overview
from .resolver import build_system_prompt, find_workflow_relpath, layers_for, read_resolved
from .safety import ActionPolicy


@dataclass
class RunRequest:
    """The generic /run payload (ADR-002 §3.2). ``model`` is optional."""

    workspace: str
    role: str
    service: Optional[str] = None
    workflow: Optional[str] = None
    input: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    # Agnostic correlation key for this flow's conversation/anti-recursion state (ADR-003):
    # an issue key, a dashboard correlation id, a scheduled-run key. Falls back per host.
    subject: Optional[str] = None
    # When true, a normal completion ends in AWAITING_HUMAN (a hand-off) instead of RESOLVED —
    # the natural end of analysis/triage flows (e.g. support-engineer). Default: resolve.
    handoff: bool = False
    # Testing/override: bypass the anti-recursion guard and run even if the subject is not
    # awaiting-agent (re-run the same ticket without re-arming). Never use in production.
    force: bool = False
    # Actions the agent may take WITHOUT human validation, for THIS run (action-kind
    # strings, e.g. ["code-read", "internal-comment"]). None → least-privilege default. The
    # autonomy level is a per-request decision, never hard-coded (ADR-002 §3.3).
    autonomy: Optional[List[str]] = None


@dataclass
class ResolvedRun:
    """Everything the agentic loop needs, pre-resolved (identity given, work improvised)."""

    system_prompt: str
    capabilities: List[str]
    workflow: Optional[str]              # advisory recipe text, or None
    layers: List[Tuple[str, str]]        # (layer, file) pairs that built the identity
    model: Optional[str]
    allowed_actions: List[str]           # autonomy granted for this run (gating allowlist)
    cortex_version: Optional[str] = None  # the version cortex.toml pins; None: the base is {root}/cortex


def _opens(root: Optional[Path], path: PurePosixPath, folder: str) -> bool:
    """Whether ``path``, or a folder of the workspace above it, is ``folder`` on the disk — both there, one
    folder: ``Agents`` where names ignore case, ``agents.`` and ``AGENTS~1`` on Windows, a link to it."""
    if root is None:
        return False
    for depth in range(1, len(path.parts) + 1):
        try:
            if os.path.samefile(root / PurePosixPath(*path.parts[:depth]), root / folder):
                return True
        except (OSError, ValueError):       # not there, or no name the disk takes: nothing to ask it
            pass
    return False


def _service_inside(service: Optional[str], team_folder: Optional[str] = "agents",
                    root: Optional[Path] = None) -> Optional[str]:
    """The service as a folder of the workspace — relative, normalised, never climbing out of it.
    Its files reach the prompt: a service naming ``../elsewhere`` would read a neighbour's. Never
    ``agents/`` nor the team's folder that renames it (ADR-013 §3.6): the workspace's tier, no service.
    ``agents`` is refused as written, as 1.7.0 refused it, and as the disk under ``root`` opens it; the
    team's folder that renames it in any case too (§9) — nor a folder whose ``agents/`` the disk opens as the team's
    folder. ``team_folder`` ``None``: not known yet — what depends on it is left to a second call."""
    if not service:
        return service
    path = PurePosixPath(service.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", service):
        raise ValueError(f"service must be a folder inside the workspace, got {service!r}")
    if not path.parts:
        return None                  # "." is the workspace itself: no service
    if path.parts[0] == "agents" or _opens(root, path, "agents"):     # Agents/ on Linux is a service of its own
        raise ValueError(f"service must be a folder of the workspace other than agents/, got {service!r}")
    if team_folder is None:
        return str(path)
    if team_folder == "agents" and _opens(root, path / "agents", "agents"):
        # its agents/ the workspace's — a link to it, or the service a link to the root: the same overlays, twice
        raise ValueError(f"service must not be the folder whose agents/ is the team's folder, agents/, got {service!r}")
    if team_folder != "agents":         # a renamed team's folder, in any case (ADR-013 §3.6, §9)
        team = tuple(folded(part) for part in PurePosixPath(team_folder).parts)
        parts = tuple(folded(part) for part in path.parts)
        if parts[:len(team)] == team or _opens(root, path, team_folder):
            raise ValueError(f"service must be a folder of the workspace other than {team_folder}/, the team's "
                             f"folder, got {service!r}")
        if parts + (folded("agents"),) == team or _opens(root, path / "agents", team_folder):
            # its agents/ the team's folder: the same overlays, twice
            raise ValueError(f"service must not be the folder whose agents/ is the team's folder, {team_folder}/, "
                             f"got {service!r}")
    return str(path)


class _ReadNow:
    def __repr__(self) -> str:
        return "READ_NOW"


# resolve_run's default pin: the project's cortex.toml, read as it is now.
READ_NOW: Any = _ReadNow()


def resolve_run(req: RunRequest, root: Path, theme: Optional[str] = None, *,
                pin: Union[Pin, None, _ReadNow] = READ_NOW) -> ResolvedRun:
    """Compile a request into a resolved bundle. ``theme`` is the workspace's active theme
    (deployment config, ``CORTEX_THEME`` — never a developer's own, in ``cortex.local.toml``); without
    one, the team's theme — and the team's file's whatever the deployment says when it locks ``theme``
    (ADR-013 §3.12). The base is the store's copy of the team's version, or ``{root}/cortex`` without
    ``cortex.toml`` (ADR-008 §3.6): a version the store lacks is a ``ValueError``, as a bad service is.

    ``pin`` is what ``cortex.toml`` said when the run was accepted — a queued run resolves as it
    was checked, whatever the file says by the time a worker takes it (``None``: it had none)."""
    root = Path(root)
    req = replace(req, service=_service_inside(req.service, None, root))
    if pin is READ_NOW:
        pin = read_pin(root)
    folder = pin.team_folder if pin is not None else "agents"
    _service_inside(req.service, folder, root)          # what depends on the team's folder, known now
    team = root / folder if folder != "agents" else None        # None: the core's default, {root}/agents
    base_root = base_for(pin)
    if pin is not None and (theme is None or pin.theme_locked):
        theme = pin.theme       # the team's file's lock wins over the deployment's CORTEX_THEME (ADR-013 §3.12)

    capabilities = derive_capabilities(root, req.service, base_root=base_root, team_folder=folder)
    system_prompt = build_system_prompt(
        role=req.role,
        service=req.service,
        theme=theme,
        root=root,
        base_root=base_root,
        team_folder=team,
        capabilities=capabilities,
        project_overview=read_project_overview(root, req.service, folder),
        workspace_services=service_index(root, base_root, active=req.service, team_folder=team),
        project_context=read_project_context(root, req.service, folder),
    )

    workflow_text: Optional[str] = None
    if req.workflow:
        wf_rel = find_workflow_relpath(req.workflow, root, base_root=base_root)
        if wf_rel:
            workflow_text = read_resolved("workflows", wf_rel, req.service, root, base_root=base_root,
                                          team_folder=team) or None

    # Validate & normalise the per-request autonomy (raises ValueError on an unknown action).
    policy = ActionPolicy.from_names(req.autonomy)

    return ResolvedRun(
        system_prompt=system_prompt,
        capabilities=capabilities,
        workflow=workflow_text,
        layers=layers_for(req.role, theme, root, base_root=base_root, team_folder=team),
        model=req.model,
        allowed_actions=sorted(k.value for k in policy.allowed),
        cortex_version=pin.version if pin is not None else None,
    )
