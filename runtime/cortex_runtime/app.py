"""Application logic for the agnostic API — framework-agnostic (ADR-002 §3.2).

Two responsibilities, both pure and testable without a web framework:
  • workspace → binding: map a workspace name to its bound ``root`` + active theme (§3.4)
  • endpoint → request: merge a project-declared alias with the request body into a
    ``RunRequest`` (the project DECLARES endpoints; it does not write engine code)

api.py is a thin FastAPI shell over ``resolve_endpoint``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from cortex_core.prompt import PROJECT_VIEW, project_view_parts

from .run import ResolvedRun, RunRequest, resolve_run


@dataclass
class WorkspaceConfig:
    """Deployment-level binding for one workspace (the warm mirror path + active theme).

    ``mcp_servers``: Claude Code ``--mcp-config`` content for this workspace's tools
    (e.g. a Jira server), shape ``{"server-name": {...server config...}}``.
    ``mcp_bindings``: maps an ActionKind value to the concrete MCP tool name(s) it unlocks
    (e.g. ``{"issue-comment": ["mcp__jira__add_comment"]}``) — so agnostic autonomy maps onto
    the deployment's real MCP tools.
    ``project_view``: the parts of the project's view a run's prompt keeps unless the request says
    otherwise (#89); ``None``, all three. An unknown part is refused here, when the deployment starts.
    """

    root: Path
    theme: Optional[str] = None
    mcp_servers: Optional[dict] = None
    mcp_bindings: Optional[dict] = None
    project_view: Optional[Sequence[str]] = None

    def __post_init__(self):
        if self.project_view is not None:
            self.project_view = project_view_parts(self.project_view)


def project_view_setting(value: Optional[str]) -> Optional[Tuple[str, ...]]:
    """``CORTEX_PROJECT_VIEW``, the deployment's default (#89): part names separated by commas, or
    ``none`` to leave the whole view out. Unset or empty: ``None``, all three parts. An unknown part
    is a ``ValueError`` naming the accepted values."""
    if value is None or not value.strip():
        return None
    if value.strip() == "none":
        return ()
    names = [name.strip() for name in value.split(",")]
    for name in names:
        if name not in PROJECT_VIEW:
            raise ValueError(f"CORTEX_PROJECT_VIEW: unknown part {name!r}, the accepted values are "
                             f"{', '.join(PROJECT_VIEW)}, separated by commas, or none")
    return project_view_parts(names)


def build_run_request(payload: Mapping[str, Any], alias: Optional[Mapping[str, Any]] = None) -> RunRequest:
    """Merge a project-declared alias (path → payload defaults) with the request body.

    The body overrides the alias for any field it provides (non-None). ``POST /run`` uses
    ``alias=None``; a domain endpoint like ``POST /support/analyze`` passes its manifest entry.
    """
    merged: Dict[str, Any] = dict(alias or {})
    for key, value in payload.items():
        if value is not None:
            merged[key] = value
    return RunRequest(
        workspace=merged["workspace"],
        role=merged["role"],
        service=merged.get("service"),
        workflow=merged.get("workflow"),
        input=merged.get("input") or {},
        model=merged.get("model"),
        autonomy=merged.get("autonomy"),
        subject=merged.get("subject"),
        handoff=bool(merged.get("handoff", False)),
        force=bool(merged.get("force", False)),
        project_view=merged.get("project_view"),
    )


def resolve_endpoint(
    registry: Mapping[str, WorkspaceConfig],
    payload: Mapping[str, Any],
    alias: Optional[Mapping[str, Any]] = None,
) -> ResolvedRun:
    """Resolve a request against the workspace registry. Raises ``KeyError`` if the
    workspace is unknown (the API layer maps that to 404)."""
    req = build_run_request(payload, alias)
    if req.workspace not in registry:
        raise KeyError(req.workspace)
    cfg = registry[req.workspace]
    return resolve_run(req, cfg.root, cfg.theme, project_view=cfg.project_view)
