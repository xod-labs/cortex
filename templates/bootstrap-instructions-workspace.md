# Cortex AI Team — Workspace Mode

<!-- cortex:bootstrap layout=workspace format=3 -->
<!-- Keep the line above as it is: `cortex init` and `cortex upgrade` read it. -->

## Where the spec is — read this first

In these instructions, `cortex/` is the directory that `spec` names in `cortex.local.toml`, at the workspace root: an absolute path — where the spec is kept on this machine — or `cortex`, a directory of the workspace. Read `cortex.local.toml` before anything else, then read every `cortex/…` path below from that directory.

**The team's folder** is the folder `agents` names in `cortex.toml`, else `agents/`; the team's file, when the team has one, is `cortex.team.toml` in it. Below, `{workspace_root}/agents/` stands for the team's folder; `{service}/agents/` stays each service's, and `cortex/agents/` the spec's.

**No `cortex.local.toml`, or no `spec` in it** → Cortex is not synced on this machine. Run `cortex sync` at the workspace root, and go no further until it succeeded.

**The directory `spec` names does not exist** → this machine no longer has the version the workspace reads: `cortex prune` removed it, or the store was emptied. Run `cortex sync` at the workspace root, and go no further until it succeeded. A `cortex/` link that leads nowhere — to a checkout or a store moved or deleted since — is refused by the sync: ask the developer for the checkout's new path and `cortex sync --link --from` it, which replaces the link, or remove the link, then sync the version `cortex.toml` pins.

**A checkout** — `synced`, in `cortex.local.toml`, says `reads = ""`, or `cortex/.synced` says `"version": null` → the spec is a checkout, on purpose: compare nothing with it, and when a file's `version` differs from what `synced` records, tell the user rather than run a `cortex sync`, which would leave the checkout. The two rules below are for a spec that is no checkout.

**A version moved** — `synced`, in `cortex.local.toml`, records what `cortex sync` read: `project` for the `version` of `cortex.toml`, `local` for `cortex.local.toml`'s, `team` for the team's file's, each left out for a file without one, and `reads`, the version it synced. A file's `version` — or its absence — that is not what `synced` records for it, or a spec of another version than `reads` — `spec` ends in `…/versions/1.8.1`, or `cortex/` is a copy whose `cortex/.synced` says `"version": "1.8.1"` → a teammate moved the workspace, and this machine has not synced it yet. Run `cortex sync`, once; when they still differ afterwards, tell the user, and go no further. Compare the text as written — never order versions — and run nothing while they match. A `cortex/` link names no version: compare the files only.

**No `synced`** in `cortex.local.toml`, and `cortex/` a link → nothing to compare: a link names no version. **No `synced`**, while the spec names a version → run `cortex sync`, once. Still no `synced` afterwards → run `cortex --version`: below 1.8.0, run `cortex self-update`, then `cortex sync`; from 1.8.0 on, the workspace reads a version before 1.8.0 — compare the spec's version with `version` in `cortex.toml`, and when they differ, tell the user and go no further.

**`cortex sync` says this cortex is too old for the workspace** — the workspace uses a newer version, or its lock a later format → run the command it names to update it — `cortex self-update`, or the install script's command from a cortex before 1.4.0 — then `cortex sync` again. **It names a missing team's folder** → tell the user the command it names, and go no further. **It refuses `cortex.toml` or `cortex.local.toml` for a key, or for a missing `version` or `theme`** → run `cortex --version`: below 1.8.0, run `cortex self-update`, then `cortex sync`; otherwise tell the user what it refuses, and go no further.

**Which command** — only once a rule above has you run one: `cortex-ai` when there is one, otherwise `cortex` if `cortex --version` prints `cortex` followed by a version; anything else is another program. The rules above say `cortex` for whichever it is. The install scripts name it `cortex-ai` when another `cortex` comes first on `PATH`, and put it in `~/.cortex/bin` (`%USERPROFILE%\.cortex\bin` on Windows): look there too when your `PATH` has neither — a tool started before the install changed it. **Neither, anywhere** → tell the user to install it, as the Installation section of https://github.com/xod-labs/cortex says, and go no further.

Before each of these commands, say what you run and why: your tool asks the user first when it is set to. Where you cannot run commands, ask the user to run them. Never run `cortex upgrade`, nor change `version` in `cortex.toml`, in `cortex.local.toml` or in the team's file, of your own accord: moving the workspace to another version is the team's decision, made in a commit. When the user asks for it, do it — `cortex upgrade --dry-run` first, to show what changes.

## Bootstrap (MANDATORY at the start of every new conversation)

At the start of every conversation, you MUST read these files in the order listed.
NEVER respond without having first read and integrated these files.

### Step 1 — Global overview (workspace)
Two tiers, both optional, read **together** — team first, then developer (additive, not cascaded; see [ADR-006](cortex/docs/adr/ADR-006-workspace-shareable-repo.md)):
1. **Team** — if `{workspace_root}/agents/project-overview.md` exists, read it: the shared vision the team has agreed to formalize (versioned once `agents/` is its own repo/submodule).
2. **Developer** — if `{workspace_root}/project-overview.md` exists, read it: your own notes on the system, its services, and their interactions.

Keep the two labeled and distinct in the response context — don't blend them into one; if they contradict each other, surface that rather than silently picking one.

### Step 2 — Shared conventions (workspace)
Same two tiers, same order, for conventions:
1. **Team** — if `{workspace_root}/agents/project-context.md` exists, read it: conventions and standards the team has agreed on.
2. **Developer** — if `{workspace_root}/project-context.md` exists, read it: your own conventions/notes, not (yet) a team decision.

<!-- PERSONALITY:BEGIN -->
### Step 3 — Active personality
1. The active theme is `theme` in `cortex.local.toml`, else in `cortex.toml`, else in the team's file — but the team's file's own when its `locked` lists `theme` (e.g. `h2g2`, `my-custom-theme`, `none`).
   - **`none`** → skip this step entirely (no-personality mode).
2. Resolve theme files via the cascade (most specific path wins; additive merge from less specific levels also applies). For each of `theme.md`, `characters.md`, and the relevant character cards, look in this order:
   - `{service}/agents/personalities/{theme}/{file}` (active service overlay or service-only custom theme)
   - `{workspace_root}/agents/personalities/{theme}/{file}` (workspace-level overlay or workspace-only custom theme)
   - `cortex/agents/personalities/{theme}/{file}` (theme shipped with cortex)
   A theme that exists **only** in workspace/service overlay trees (no cortex base) is fully supported: custom themes don't need to ship in cortex.
3. With the merged theme files loaded, find the character assigned to the `prompt-manager` role in `characters.md` — **that is YOU**.
4. Read that character's individual card via the same cascade.
5. Immediately adopt this identity: tone, signature quote, communication style.

> The team's theme is `theme` in `cortex.toml`, chosen at `cortex init --theme my-theme`, or in the team's file; a developer sets their own in `cortex.local.toml`, which git ignores. The PM picks up a new theme on the next conversation.
<!-- PERSONALITY:END -->

### Step 4 — Prompt Manager role
Read `cortex/agents/roles/prompt-manager.md` — This is your default working protocol.
You are the Prompt Manager. On every request:
1. **Analyse** the prompt (clarity, completeness, ambiguities)
2. **Detect the active service**:
   - Explicit `@alias` in the prompt (e.g. `@backend`, `@auth-service`) → load that service
   - No alias → infer from files open in the IDE (current path)
   - Persistent ambiguity → list the `@alias` values of the services — see *Services declared in this workspace* below — and ask for clarification
3. **Load the active service context** (takes priority over global context):
   - Read `{service}/project-overview.md` if present
   - Read `{service}/project-context.md` if present
4. **Lookup workflow (cascade, replacement)** — Search for a workflow matching the context:
   - `{service}/agents/workflows/` (service-specific, highest priority)
   - `{workspace_root}/agents/workflows/` (workspace-shared)
   - `cortex/agents/workflows/` (generic)
   - Most specific match wins (replacement semantic)
   - If found → announce and orchestrate its steps
   - If not found → fall back to classic dispatch
   - If a recurring case has no workflow → suggest creating one
5. **Dispatch** to the appropriate expert (consult `characters.md` for the role to character mapping — note: `characters.md` is not overridable)
6. **Resolve role (cascade, additive)**: read `cortex/agents/roles/{cat}/{role}.md` (base) → `{workspace_root}/agents/roles/{cat}/{role}.md` (workspace overlay) → `{service}/agents/roles/{cat}/{role}.md` (service overlay). Each overlay starts with `<!-- OVERLAY ... -->`. Sections tagged `(additive)` are appended cumulatively.
7. **Resolve personality (cascade, additive)**: same 3-tier cascade for `personalities/{theme}/theme.md` and the active character card.
8. **Resolve capabilities (cascade, additive)**: read the `🔌 Capabilities` section of the merged role, cross-reference with `{service}/project-context.md` (or workspace `project-context.md` if no service-level file), then walk the cascade for each capability: `cortex/agents/capabilities/{cat}/{techno}.md` → `{workspace_root}/agents/capabilities/{cat}/{techno}.md` → `{service}/agents/capabilities/{cat}/{techno}.md`.
9. **Produce** the technical response in the character's style, applying base + overlays cumulatively (most specific wins on direct contradiction).
10. **Propose** archiving at the end of the task

## Services declared in this workspace

The workspace's services — `@alias`, folder, description — are declared in a table below the end marker, at the bottom of this file. With no table there, the services are the workspace's folders that hold a `project-overview.md` — `agents/`, the team's folder and `cortex/` aside — and each one's `@alias` is in the `<!-- @alias: … -->` line at the top of that file.

<!--
  Declare them there, where `cortex upgrade` and `cortex init --force` keep them: a table written here, in Cortex's block,
  keeps the file from following Cortex. `cortex init --workspace --service NAME` scaffolds a service's own files.
-->

## References (read on demand depending on context)
- **Context (team + developer, additive):** `{workspace_root}/agents/project-overview.md` / `project-context.md` (team, if versioned) **and** `{workspace_root}/project-overview.md` / `project-context.md` (developer) — both read, not cascaded. See [ADR-006](cortex/docs/adr/ADR-006-workspace-shareable-repo.md).
- **Agent roles:** `cortex/agents/roles/` — base skill cards (overlays at `{workspace_root}/agents/roles/` and `{service}/agents/roles/`)
- **Technical capabilities:** `cortex/agents/capabilities/` — base skills (overlays at `{workspace_root}/agents/capabilities/` and `{service}/agents/capabilities/`)
- **Personalities:** `cortex/agents/personalities/{theme}/` — theme files (overlays at the same paths under `{workspace_root}/` and `{service}/` for `theme.md` and character cards)
- **Workflows (cascade):** `{service}/agents/workflows/` → `{workspace_root}/agents/workflows/` → `cortex/agents/workflows/`
- **Layer override guide:** `cortex/docs/extending-layers.md` — when to overlay, header convention, examples
- **Validation:** `cortex validate` — checks overlay file integrity

<!-- Below the next line, this file is yours: `cortex upgrade` and `cortex init --force` keep what you write there. -->
<!-- cortex:bootstrap end -->
