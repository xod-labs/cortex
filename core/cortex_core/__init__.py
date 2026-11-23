"""cortex-core — the one implementation of the Cortex cascade in code (ADR-007).

Everything Cortex computes from the spec lives here: cascade resolution, merge
semantics, role/workflow/character lookup, the capability catalog, prompt assembly
and overlay validation. The runtime and the validator's entry point consume it.

Two rules hold for every module of this package:

- **Standard library only**, Python 3.11 or later — it runs from source, uninstalled.
- **It never imports the runtime.** The runtime depends on the core, not the reverse.
"""

from .catalog import capability_catalog, capability_dirs
from .prompt import LAYER_SEPARATOR, PROJECT_VIEW, build_system_prompt, layers_for, project_view_parts
from .resolver import (
    ADDITIVE_SEPARATOR,
    MergeSemantic,
    character_for_role,
    default_base_root,
    find_role_relpath,
    find_workflow_relpath,
    read_resolved,
    resolve_layer,
    semantic_for,
)
from .workspace import Refusal, refusal

__all__ = [
    "ADDITIVE_SEPARATOR",
    "LAYER_SEPARATOR",
    "MergeSemantic",
    "PROJECT_VIEW",
    "Refusal",
    "build_system_prompt",
    "capability_catalog",
    "capability_dirs",
    "character_for_role",
    "default_base_root",
    "find_role_relpath",
    "find_workflow_relpath",
    "layers_for",
    "project_view_parts",
    "read_resolved",
    "refusal",
    "resolve_layer",
    "semantic_for",
]
