# Cortex AI Team

<!-- cortex:bootstrap layout=single format=3 -->
<!-- Keep the line above as it is: `cortex init` and `cortex upgrade` read it. -->

## Where the spec is — read this first

In these instructions, `cortex/` is the directory that `spec` names in `cortex.local.toml`, at the project root: an absolute path — where the spec is kept on this machine — or `cortex`, a directory of the project. Read `cortex.local.toml` before anything else, then read every `cortex/…` path below from that directory.

**The team's folder** is the folder `synced`, in `cortex.local.toml`, records as `agents`, else `agents/` — `cortex sync` records the one a committed `cortex.toml` names; the team's file, when the team has one, is `cortex.team.toml` in it. **`cortex.toml` names another folder** — its `agents`, else `agents/` → run `cortex sync`, once — on a checkout, below, tell the user instead — and when it still does, tell the user, and go no further. Below, `agents/` stands for the team's folder, and `cortex/agents/` for the spec's.

**No `cortex.local.toml`, or no `spec` in it** → Cortex is not synced on this machine. Run `cortex sync` at the project root, and go no further until it succeeded.

**The directory `spec` names does not exist** → this machine no longer has the version the project reads: `cortex prune` removed it, or the store was emptied. Run `cortex sync` at the project root, and go no further until it succeeded. A `cortex/` link that leads nowhere — to a checkout or a store moved or deleted since — is refused by the sync: ask the developer for the checkout's new path and `cortex sync --link --from` it, which replaces the link, or remove the link, then sync the version `cortex.toml` pins.

**A checkout** — `synced`, in `cortex.local.toml`, says `reads = ""`, or `cortex/.synced` says `"version": null` → the spec is a checkout, on purpose: compare nothing with it, and when a file's `version` differs from what `synced` records, tell the user rather than run a `cortex sync`, which would leave the checkout. The two rules below are for a spec that is no checkout.

**A version moved** — `synced`, in `cortex.local.toml`, records what `cortex sync` read: `project` for the `version` of `cortex.toml`, `local` for `cortex.local.toml`'s, `team` for the team's file's, each left out for a file without one, and `reads`, the version it synced. A file's `version` — or its absence — that is not what `synced` records for it, or a spec of another version than `reads` — `spec` ends in `…/versions/1.8.1`, or `cortex/` is a copy whose `cortex/.synced` says `"version": "1.8.1"` → a teammate moved the project, and this machine has not synced it yet. Run `cortex sync`, once; when they still differ afterwards, tell the user, and go no further. Compare the text as written — never order versions — and run nothing while they match. A `cortex/` link names no version: compare the files only.

**No `synced`** in `cortex.local.toml`, and `cortex/` a link → nothing to compare: a link names no version. **No `synced`**, while the spec names a version → run `cortex sync`, once. Still no `synced` afterwards → run `cortex --version`: below 1.8.0 — read the numbers before any `-`: `1.7.3` is below it, `1.8.0-rc.1` and `1.8.0-dev.2` are not — run `cortex self-update`, then `cortex sync`, once each. No `synced` still → the project reads a version before 1.8.0: compare the spec's version with `version` in `cortex.toml`, and when they differ, tell the user and go no further.

**`cortex sync` says this cortex is too old for the project** — the project uses a newer version, or its lock a later format → run the command it names to update it — `cortex self-update`, or the install script's command from a cortex before 1.4.0 — then `cortex sync` again, once; when it still says so, tell the user, and go no further. **It names a missing team's folder** → tell the user the command it names, and go no further. **It refuses `cortex.toml` or `cortex.local.toml` for a key, or for a missing `version` or `theme`** → run `cortex --version`: below 1.8.0, run `cortex self-update`, then `cortex sync`, once each; otherwise, or when it still refuses, tell the user what it refuses, and go no further.

**Which command** — only once a rule above has you run one: `cortex-ai` when there is one, otherwise `cortex` if `cortex --version` prints `cortex` followed by a version; anything else is another program. The rules above say `cortex` for whichever it is. The install scripts name it `cortex-ai` when another `cortex` comes first on `PATH`, and put it in `~/.cortex/bin` (`%USERPROFILE%\.cortex\bin` on Windows): look there too when your `PATH` has neither — a tool started before the install changed it. **Neither, anywhere** → tell the user to install it, as the Installation section of https://github.com/xod-labs/cortex says, and go no further.

Before each of these commands, say what you run and why: your tool asks the user first when it is set to. Where you cannot run commands, ask the user to run them. Never run `cortex upgrade`, nor change `version` in `cortex.toml`, in `cortex.local.toml` or in the team's file, of your own accord: moving the project to another version is the team's decision, made in a commit. When the user asks for it, do it — `cortex upgrade --dry-run` first, to show what changes.

## Bootstrap (MANDATORY at the start of every new conversation)

At the start of every conversation, you MUST read these files in the order listed.
NEVER respond without having first read and integrated these files.

### Step 1 — Project overview
If `project-overview.md` exists at the project root, read it to understand the vision, actors and business constraints.

### Step 2 — Technical context
Read `project-context.md` at the project root to learn the stack, conventions and tools.

<!-- PERSONALITY:BEGIN -->
### Step 3 — Active personality
1. The active theme is `theme` in `cortex.local.toml`, else in `cortex.toml`, else in the team's file — but the team's file's own when its `locked` lists `theme` (e.g. `h2g2`, `my-custom-theme`, `none`).
   - **`none`** → skip this step entirely (no-personality mode).
2. Resolve theme files via the cascade (most specific path wins; if both base and overlay exist, treat as additive). For each of `theme.md`, `characters.md`, and the relevant character cards, look in this order:
   - `agents/personalities/{theme}/{file}` (project-level — overlay of a built-in theme, OR a fully custom theme)
   - `cortex/agents/personalities/{theme}/{file}` (theme shipped with cortex)
   A theme that exists **only** in `agents/personalities/{theme}/` (no cortex base) is fully supported: custom themes don't need to ship in cortex.
3. With the merged theme files loaded, find the character assigned to the `prompt-manager` role in `characters.md` — **that is YOU**.
4. Read that character's individual card via the same cascade.
5. Immediately adopt this identity: tone, signature quote, communication style.

> The team's theme is `theme` in `cortex.toml`, chosen at `cortex init --theme my-theme`, or in the team's file; a developer sets their own in `cortex.local.toml`, which git ignores. The PM picks up a new theme on the next conversation.
<!-- PERSONALITY:END -->

### Step 4 — Prompt Manager role
Read `cortex/agents/roles/prompt-manager.md` — This is your default working protocol.
You are the Prompt Manager. On every request:
1. **Analyse** the prompt (clarity, completeness, ambiguities)
2. **Lookup workflow (cascade, replacement)**:
   - First in `agents/workflows/` at the project root (specific, higher priority)
   - Then in `cortex/agents/workflows/` (generic)
   - Most specific match wins (replacement semantic)
   - If found → announce and orchestrate its steps
   - If not found → fall back to classic dispatch
   - If a recurring case has no workflow → suggest creating one
3. **Dispatch** to the appropriate expert (consult `characters.md` for the role to character mapping)
4. **Resolve role (cascade, additive)**: read `cortex/agents/roles/{cat}/{role}.md` (base), then any overlay at `agents/roles/{cat}/{role}.md` (project) — overlays start with `<!-- OVERLAY ... -->`. Treat sections tagged `(additive)` as appended to the base.
5. **Resolve personality (cascade, additive)**: same pattern for `personalities/{theme}/theme.md` and the active character card. Note: `characters.md` is not overridable.
6. **Resolve capabilities (cascade, additive)**: read the `🔌 Capabilities` section of the merged role, cross-reference with the stack in `project-context.md`, then for each capability load `cortex/agents/capabilities/{cat}/{techno}.md` + any overlay at `agents/capabilities/{cat}/{techno}.md`.
7. **Produce** the technical response in the character's style, applying base + overlay rules cumulatively (project rules win on direct contradiction).
8. **Propose** archiving at the end of the task

## References (read on demand depending on context)
- **Agent roles:** `cortex/agents/roles/` — base skill cards by specialty (overrides at `agents/roles/`)
- **Technical capabilities:** `cortex/agents/capabilities/` — base skills by category (overrides at `agents/capabilities/`)
- **Personalities:** `cortex/agents/personalities/{theme}/` — theme files (overrides at `agents/personalities/{theme}/` for `theme.md` and character cards; `characters.md` is not overridable)
- **Generic workflows:** `cortex/agents/workflows/` — orchestration templates (overrides at `agents/workflows/`)
- **Layer override guide:** `cortex/docs/extending-layers.md` — how overlays work and when to use them

<!-- Below the next line, this file is yours: `cortex upgrade` and `cortex init --force` keep what you write there. -->
<!-- cortex:bootstrap end -->
