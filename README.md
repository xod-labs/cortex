# Cortex

> [!IMPORTANT]
> **Cortex moved to [`xod-labs/cortex`](https://github.com/xod-labs/cortex) on 2026-10-30**, from `adorey/cortex`. The old addresses redirect, so nothing you set up stops working. [Moving to xod-labs](docs/moving-to-xod-labs.md) says what to update: your install, your clone, your scripts.

<p align="center">
  <img src="assets/logo.png" alt="Cortex — AI Agent Framework" width="200" height="300" />
</p>

Cortex is a team of specialized AI agents that integrates into any project. A Prompt Manager reframes each request and hands it to the right expert — architect, lead backend, DBA, security engineer, product owner, tech writer… — who works with the technical skills your stack calls for, your project's conventions and, if you like, a personality. It has **two complementary halves**:

- **The spec** — a portable, host-agnostic Markdown cascade (`agents/…`) that any AI coding tool (Claude Code, Copilot, Cursor, Codex…) reads as its system instructions. This is the design-time layer.
- **The runtime** — [`cortex-runtime`](runtime/README.md), a deployable engine that *executes* that spec as a service: a thin agnostic API, an agentic loop, persistent state, an optional security perimeter, and asynchronous execution.

> **The runtime CONSUMES the spec. The spec NEVER depends on the runtime.** ([ADR-002](docs/adr/ADR-002-cortex-runtime.md))
> The Markdown cascade stays a pure, portable knowledge layer — you can adopt the spec alone and never touch the engine.

## 🧩 How a project uses Cortex

One command, `cortex`, installed once per machine, brings the spec to your projects. No copy of Cortex goes inside them — no submodule, no clone — and they need no Python and no Bash ([ADR-008](docs/adr/ADR-008-cortex-binary.md)):

```
~/.cortex/                     ← the Cortex home ($CORTEX_HOME moves it)
├── bin/cortex                 ← the command, put there by install.sh or install.ps1
├── versions/X.Y.Z/            ← the store: the spec of each version, read-only, filled on demand
└── projects.toml              ← the registry: this machine's Cortex projects

my-project/
├── cortex.toml                ← committed: the Cortex version the project pins, the team's theme
├── cortex.lock                ← committed: that version's digest, checked at every sync
├── cortex.local.toml          ← git-ignored: where the spec is on this machine, your own theme
├── CLAUDE.md                  ← committed: your AI tool's bootstrap file — or AGENTS.md, …
├── project-overview.md        ← committed: vision, stakeholders, business constraints
├── project-context.md         ← committed: stack, conventions, tools
└── agents/                    ← committed, optional: your overlays on the spec's layers
```

Your AI tool reads the bootstrap file at the start of every conversation, and the bootstrap sends it to the spec of the version the project pins, then to your project's own files. `cortex sync` puts that version in the store and tells the project where it is; `cortex upgrade` moves the project to another version; `cortex self-update` moves the command itself.

## 🚀 Concept

Each agent is composed of **5 independent layers**:

```
┌─────────────────────────────────┐
│   project-overview.md           │  ← Vision, stakeholders, business constraints
│   project-context.md            │  ← Stack, conventions, tools
├─────────────────────────────────┤
│   capabilities/{techno}.md      │  ← Loadable technical skills (PHP, Docker…)
├─────────────────────────────────┤
│   personalities/{theme}/        │  ← Optional personality (e.g. H2G2)
├─────────────────────────────────┤
│   roles/{role}.md               │  ← Generic business skills
├─────────────────────────────────┤
│   workflows/{context}.md        │  ← Multi-agent orchestration templates
└─────────────────────────────────┘
```

> *"Let's explain this as if Earth had just been destroyed and we had to start from scratch."* — Arthur Dent

| Layer | Answers | Example |
|---|---|---|
| `roles/` | **WHAT** to do | "A lead backend structures, reviews, mentors" |
| `capabilities/` | **WHAT I CAN DO** | "In PHP: PSR-12, dependency injection..." |
| `personalities/` | **WHO** you are | "Hactar, methodical, elegant" |
| `project-overview.md` | **WHY** you work | "Mission: B2B platform, stakeholders, business constraints" |
| `project-context.md` | **WHERE / HOW** you work | "This project: Symfony 7.2, PHP 8.3, MySQL 8" |
| `workflows/` | **IN WHAT ORDER and WITH WHOM** | "Feature dev: architect → backend → QA → security → doc" |

This separation allows:
- Changing **personality** (H2G2, Breaking Bad, your own…) without touching the skills
- Reusing **roles** across any tech stack
- Sharing **best practices** for a technology across all projects that use it
- Customizing the **project context** without modifying the agents
- Defining reusable **workflows** (generic in cortex) or project-specific (in the host project via `agents/workflows/`)

### 🪜 Layered overrides (cascade)

Every layer (`roles/`, `capabilities/`, `personalities/`, `workflows/`) supports a **3-tier cascade**: a host project can extend any base file with an overlay at workspace or service level, without forking cortex itself.

```
{service}/agents/{layer}/...                    ← priority 1 (most specific)
{workspace_root}/agents/{layer}/...             ← priority 2 (workspace mode only)
cortex/agents/{layer}/...                       ← priority 3 (default, ships with cortex)
```

`cortex/` names the spec: the directory that `spec` names in the project's `cortex.local.toml`, written by `cortex sync` (see [After a clone](#after-a-clone)). It is a name, not necessarily a directory of the project.

Overlays are **additive** by default (rules are appended to the base), except for `workflows/` which use **replacement** (sequence-level override). See [docs/extending-layers.md](docs/extending-layers.md) for the practical guide and [ADR-001](docs/adr/ADR-001-layered-overrides.md) for the formal contract.

## 🔧 Installation

> This section covers the **design-time spec**. To run the engine, jump to [The Runtime](#️-the-runtime).

```bash
curl -fsSL https://raw.githubusercontent.com/xod-labs/cortex/main/install.sh | sh                       # Linux, macOS
curl -fsSL https://raw.githubusercontent.com/xod-labs/cortex/main/install.sh | sh -s -- X.Y.Z           # a given version
curl -fsSL https://raw.githubusercontent.com/xod-labs/cortex/main/install.sh | sh -s -- --name cortex   # force the name
```

```powershell
irm https://raw.githubusercontent.com/xod-labs/cortex/main/install.ps1 | iex                            # Windows PowerShell
# With options — -Version X.Y.Z, -Name cortex, -NoModifyPath — run it as a script block:
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/xod-labs/cortex/main/install.ps1))) -Version X.Y.Z
```

- **What it does.** The script downloads the release asset of this machine — the latest release, or the version given — checks it against the release's `SHA256SUMS`, stops on a mismatch, and installs the binary. It needs no administrator right.
- **Where it goes.** Into `~/.cortex/bin` (`%USERPROFILE%\.cortex\bin` on Windows). `install.ps1` adds that directory to your user `PATH` — `-NoModifyPath`, or `CORTEX_NO_MODIFY_PATH=1`, leaves it alone. `install.sh` edits no file of yours: it prints the line to add to your shell profile.
- **Its name.** When another `cortex` command comes first on `PATH`, the scripts install it as **`cortex-ai`**: use that name wherever this README says `cortex`. `--name cortex` (`-Name cortex`) forces the name. The command behaves the same under either.
- **What it runs on.** Linux x86_64 and aarch64 (glibc 2.28 or later), macOS on Apple silicon, Windows x86_64 — Windows on Arm runs the x86_64 build. Nothing else: no Python, no Bash, and Git only for your project itself.
- **Where Cortex lives.** `CORTEX_HOME` moves `~/.cortex` — the binary, the store and the registry — elsewhere.
- **Later.** `cortex self-update` replaces the binary with the latest release's ([Update the binary](#update-the-binary)); running the script again works too. `cortex --version` prints the installed version.

## 🏁 Setup

### A first project

```bash
cd my-project/
cortex init                         # H2G2 theme, GitHub Copilot's .github/copilot-instructions.md
cortex init --tool claude           # Claude Code's CLAUDE.md
cortex init --no-personality        # neutral professional agents (no theme)
```

`--tool` picks the bootstrap file, the one your AI tool reads first:

| `--tool` | Bootstrap file |
|---|---|
| `copilot` — the default for a new project | `.github/copilot-instructions.md` |
| `cursor` | `.cursor/rules/cortex.mdc`, opening on the frontmatter — `alwaysApply: true` — that has Cursor read it in every conversation |
| `claude` | `CLAUDE.md` |
| `agents` — Codex, and any tool that reads it | `AGENTS.md` |
| `custom` | the path `--instructions-file PATH` gives |

`cortex init` writes `cortex.toml` — the binary's own version and the team's theme — adds `cortex.local.toml` to `.gitignore`, and runs `cortex sync`, which puts the spec in the store, writes `cortex.lock`, and writes `spec` in `cortex.local.toml`. It then writes the bootstrap file, `project-overview.md` and `project-context.md`, each when it is missing. Fill those two in: they are what the agents know of your project.

The bootstrap file is in two parts. **Cortex's block** runs from the top to the line `<!-- cortex:bootstrap end -->`: `cortex init --force` and `cortex upgrade` write it again, so keep its marker line as it is — `<!-- cortex:bootstrap layout=single format=2 digest=… -->` under the title, from which `cortex init` reads the layout and format, and `cortex upgrade`, by its digest, whether anyone changed the block. **Below the end marker, the file is yours**: write there what your tool must read first, and every later Cortex keeps it byte for byte ([ADR-012](docs/adr/ADR-012-bootstrap-block.md)). A rule of your own machine goes in your tool's user-level instructions (`~/.claude/CLAUDE.md`, …) or in your `project-context.md`; a team convention, in `project-context.md` — `agents/project-context.md` in a workspace.

Then talk to the Prompt Manager in your AI tool — `@Oolon` with the H2G2 theme, `@prompt-manager` without one:

```
@Oolon I want to add a pagination system on the /contracts API
```

[Getting Started](docs/getting-started.md) goes through every step, with examples of the two context files and how the dispatch works.

### After a clone

Each developer runs `cortex sync` once in a fresh clone, as they would `npm install` — and when they do not, their assistant does ([What the assistant does on its own](#what-the-assistant-does-on-its-own)). It writes `spec` in `cortex.local.toml`: where the spec is on this machine. By default nothing of the spec enters the project — the AI tool reads it in place, and `cortex/` in the bootstrap, the docs and every overlay's `Base:` header names that directory.

| Mode | In the project | `spec` |
|---|---|---|
| `store` (default) | nothing — the tool reads the spec in place | the store's path, `~/.cortex/versions/X.Y.Z` |
| `link` | `cortex/`, a link to the store — a junction on Windows | `cortex` |
| `copy` | `cortex/`, a read-only copy | `cortex` |

`cortex sync --link` or `--copy` picks a mode for one run; `sync = "link"` or `sync = "copy"` in `cortex.toml` — `cortex init --link` or `--copy` writes it — makes it the team's. Which mode a tool needs is measured, not assumed: so far Claude Code only, which asks once per session for permission to read the store, and needs nothing in `copy` mode. `claude_access = true` in `cortex.toml` — offered by `cortex init --tool claude` — has `cortex sync` let it read the store without asking; `cortex sync --claude-access` does it for you alone. The table is in [Moving to the `cortex` binary](docs/migrating-to-the-binary.md#7-which-mode-your-tool-needs).

### A workspace (several services)

In a monorepo, or a folder holding several repositories, run `cortex init --workspace` at the workspace root. Nothing of Cortex sits next to the services:

```bash
cd workspace/
cortex init --workspace --service backend --service apps/frontend
```

Each `--service` is a folder of the workspace and gets its own `project-overview.md` and `project-context.md`, with the folder's name as its `@alias` (`apps/frontend` → `@frontend`). On a terminal with no `--service`, the command asks for the services. The workspace root gets `cortex.toml`, `cortex.lock`, `cortex.local.toml` and the bootstrap file, written from the workspace template — its marker says `layout=workspace`. When `agents/` is its own git repository, the team's `agents/project-overview.md` and `agents/project-context.md` are scaffolded too ([ADR-006](docs/adr/ADR-006-workspace-shareable-repo.md)).

To target a service in a prompt, use its alias:
```
@backend Add a pagination endpoint on /users
@frontend Create a sortable table component
```
If no alias is mentioned, Cortex infers the service from the active file context.

### What to commit

| File | Committed | What it holds |
|---|---|---|
| `cortex.toml` | yes | `version`, the Cortex version the project pins; the team's `theme`; optionally `sync` and `claude_access` |
| `cortex.lock` | yes | the pinned version and the digest of its spec — `cortex sync` writes it, nobody edits it |
| the bootstrap file | yes | `CLAUDE.md`, `AGENTS.md`, … — what your AI tool reads first |
| `project-overview.md`, `project-context.md` | yes | your project's vision and stack — per service, too, in a workspace |
| `agents/` | yes | your overlays, if any |
| `cortex.local.toml` | no — git-ignored | `spec`, where the spec is on this machine; your own `theme` and `claude_access`, which win over the team's |
| `cortex/` | no — git-ignored | the link or the copy, in `link` or `copy` mode |
| `.claude/settings.local.json` | no — git-ignored | where `cortex sync` lets Claude Code read the store, with `claude_access` |

A workspace root that is no git repository — a folder holding the services' repositories — has nowhere to commit `cortex.toml`: each developer runs `cortex init --workspace` in their own root instead of `cortex sync`.

### Coming from a git submodule or clone

`cortex init` refuses while `cortex/` is a git submodule or a clone of Cortex, and prints the commands that remove it. [Moving to the `cortex` binary](docs/migrating-to-the-binary.md) covers the switch.

## 🧰 Commands

`cortex --help` lists the commands, and `cortex <command> --help` the options of one. `cortex --version` (`-V`) prints the version — with `--verbose`, the spec archives it knows the checksums of. A command that works on a project finds it from the current directory up: the nearest `cortex.toml`, the workspace root in workspace mode.

> `cortex upgrade`, `cortex self-update`, `cortex prune` and `cortex.lock` are from 1.4.0. **1.5.0 adds the project registry**: `cortex project`, the registration by `cortex init` and `cortex sync`, `cortex prune` with no `DIR`, and `cortex upgrade --all`, `--branch` and `--commit`.

### `cortex init`

```text
cortex init [DIR] [--tool TOOL] [--instructions-file PATH] [--theme THEME] [--no-personality]
            [--workspace] [--service NAME]... [--link | --copy]
            [--claude-access | --no-claude-access] [--force] [--from PATH]
```

Makes a directory a Cortex project — see [A first project](#a-first-project).

| Option | What it does |
|---|---|
| `DIR` | the project's root — the current directory by default |
| `--tool TOOL` | the AI tool: `copilot` (the default for a new project), `cursor`, `claude`, `agents`, `custom`; for a project that has a bootstrap file, the tool whose file is there |
| `--instructions-file PATH` | the bootstrap file of `--tool custom` |
| `--theme THEME` | the team's personality theme — `h2g2` by default, or the one `cortex.toml` names: one the pinned version ships, or one of your own in `agents/personalities/` |
| `--no-personality` | no personality layer, `theme = "none"` — wins over `--theme` |
| `--workspace` | workspace mode: several services under one root |
| `--service NAME` | a service of the workspace, a folder — repeatable; asked for on a terminal when omitted |
| `--link`, `--copy` | the team's sync mode: `sync = "link"` or `sync = "copy"` in `cortex.toml`, with `/cortex` added to `.gitignore` |
| `--claude-access` | let Claude Code read the spec without asking: `claude_access = true` in `cortex.toml` — asked on a terminal with `--tool claude` |
| `--no-claude-access` | do not ask, and leave it off |
| `--force` | replace an existing bootstrap file, kept as `FILE.bak` (`FILE.bak.N` when a `.bak` is there) |
| `--from PATH` | take the templates and the spec from a checkout of Cortex, as `cortex sync --from` does — for contributors |

Run again, `cortex init` keeps the bootstrap file, and `cortex.toml` with its version: it changes only the keys an option names. Once it succeeded, it registers the project, as `cortex sync` does.

### `cortex sync`

```text
cortex sync [--store | --link | --copy] [--locked] [--from PATH] [--claude-access | --no-claude-access]
```

Puts the version `cortex.toml` pins in the store — from the binary itself when it is the binary's own version, otherwise downloaded and checked against its release's `SHA256SUMS` — checks it against `cortex.lock`, and writes `spec` in `cortex.local.toml`. A binary serves every version from 1.0.0 up to its own: a project pinned to a newer one is refused, with the `cortex self-update` command that installs it.

| Option | What it does |
|---|---|
| `--store` | read the spec where it is, in the store — the default |
| `--link` | link `cortex/` to it: a symbolic link, a junction on Windows |
| `--copy` | copy it into `cortex/`, read-only |
| `--locked` | never write `cortex.lock`: stop when it is missing, or names another version than `cortex.toml` — for CI, before `cortex validate --strict` |
| `--from PATH` | use a checkout of Cortex instead of the store: no version is checked, no lock read or written — for contributors |
| `--claude-access` | let Claude Code read the spec without asking, from now on, for you: `claude_access = true` in `cortex.local.toml` |
| `--no-claude-access` | stop it: `claude_access = false` in `cortex.local.toml` |

**`cortex.lock`.** Missing, sync writes it, and says to commit it; naming another version — the pin moved — it rewrites it, and says so. Of the pinned version with another digest, the version is put in the store again, and refused if its files still differ from what the team locked. A lock of a later format is refused, as a newer pin is.

**The registry.** Once it succeeded, `cortex sync` adds the project to the registry when it is not listed, and says so once: *registered as acme — `cortex project list` lists this machine's projects*. `--from` and `--locked` register too, and so does `cortex upgrade`, which syncs. It never renames or moves an entry. A registry that cannot be read or written is a warning, never a failed sync. `CORTEX_NO_REGISTER` turns registration off.

### `cortex upgrade`

```text
cortex upgrade [VERSION] [--dry-run] [--force] [--instructions-file PATH] [--branch NAME] [--commit --message MESSAGE]
cortex upgrade --all [VERSION] [--dry-run] [--force] [--branch NAME] [--commit --message MESSAGE]
```

Moves the project to `VERSION`, or to the binary's own version — never down to it: a project pinned to a newer version moves down only when `VERSION` names it. It puts the target in the store, then says what changes — the roles, capabilities, personalities and workflows added, removed and changed, the templates, and where the release notes are — and checks the project's overlays against the target: an overlay that fails stops it, having changed nothing. Then it writes the bootstrap file again when nobody changed it and the target's template differs — one your team adapted is kept, and the command names `cortex init --force` — writes `version` in `cortex.toml`, and syncs, which rewrites `cortex.lock`. If anything fails, `cortex.toml`, `cortex.lock` and the bootstrap file are put back as they were. It ends with what to commit. Run on the version already pinned, it finishes what an upgrade cut short left.

With no `VERSION`, it also says when a release newer than the binary is out — `CORTEX_NO_UPDATE_CHECK` turns that question off. It never moves the binary.

| Option | What it does |
|---|---|
| `VERSION` | the version to move to, `X.Y.Z` — the binary's own by default |
| `--dry-run` | say what would change and check the overlays, and change nothing in the project; it exits as the run would |
| `--force` | move the project even if its overlays fail against the target |
| `--instructions-file PATH` | the bootstrap file to write again, when it is no known tool's |
| `--all` | every project of the registry, in name order, to the same target |
| `--branch NAME` | create and switch to the branch `NAME` in each project that is a git work tree, once its checks pass and before its first write — none for a project with nothing to write, nor where git ignores every file written; a project already on `NAME` is written there, and one where `NAME` exists while it is on another branch is refused. Nothing is stashed |
| `--commit --message MESSAGE` | commit, in each project the upgrade wrote files in — moved, or finished on its pinned version — those files and nothing else, with `MESSAGE` — required: your team's commit convention is its own. A project where `cortex.toml`, or a bootstrap file the upgrade wrote, held changes of yours before it — staged or not — gets no commit, and the command says so; the lock, written whole from the pin, does not count, and a file it wrote outside the work tree is left out, and named where it is, to commit there. Nothing is pushed |

**`--all`** gives each registered project the upgrade above. A project that is not `ok` in `cortex project list` is skipped, and said; the release check asks once. A project that refuses — overlays that fail, a sync that refuses, a pin newer than the binary — does not stop the others. The command ends with one line per project — moved, already on the target, refused and why, skipped and why — and what each must commit, by path, and exits 1 if any project refused — one skipped does not. An empty registry moves nothing, and exits 0; a `VERSION` this cortex does not serve is refused once, before any project. Nothing is committed without `--commit`, and the command says so; with it, a project outside git, one whose `cortex.toml` git does not track, and one where git ignores a file the upgrade wrote are said, and nothing is committed there, while a commit git refuses — a hook, say — is that project's refusal, its upgrade done, and its files stay staged. Without git on `PATH`, `--branch` and `--commit` are refused before anything changes. `--dry-run` creates no branch, and `--force` is passed to each project. `--instructions-file` names one project's file, and is refused with `--all`. Interrupted — Ctrl-C — it still ends with its summary: the project cut short, which may be half written, its branch if one was made, and the projects not reached; it exits 130. The same command again finishes the project cut short, and names the files a run before it left to commit — `git status` there says what.

### `cortex self-update`

```text
cortex self-update [VERSION] [--dry-run]
```

Replaces the binary that runs — under whichever name, `cortex` or `cortex-ai` — with a release's: the latest by default. The asset of the binary's own platform is downloaded from where the store downloads, checked against the release's `SHA256SUMS`, and run once before it takes the old one's place; on Windows the old one is renamed aside, and removed at the next start. It replaces the file it runs from, where it is: a binary kept in a directory named after its version — `/opt/cortex-X.Y.Z/cortex-ai`, say — gets another version under that name. A binary in a directory you cannot write in — one an administrator or a package manager installed — is refused, and so is a source checkout. It moves no project.

| Option | What it does |
|---|---|
| `VERSION` | the version to install, `X.Y.Z` — the latest release by default; an older one is installed as a downgrade, and said |
| `--dry-run` | download, check and run the new binary, and replace nothing |

### `cortex prune`

```text
cortex prune [DIR...] [--keep VERSION]... [--dry-run]
```

Removes from the store the versions no project uses. It keeps the version each project pins and the one it reads now — a teammate's bump pulled and not synced yet — the binary's own version, and each `--keep`, and lists the versions kept, each with the projects that pin it, and the versions removed.

- **With `DIR...`**, the projects are those under each directory: every `cortex.toml` up to six levels down, past `.git`, `node_modules`, `vendor`, `.venv` and a synced `cortex/`. The registry is neither read nor written.
- **With no `DIR`**, the projects are the registry's. An entry whose project is gone — `missing`, or `no-project` with nothing of Cortex left — is dropped from the registry, and said; an `unreadable` one is kept, and named, with the versions that can be read of it; so is a `no-project` one whose `cortex.local.toml` still reads a version — a branch without Cortex checked out — with that version. The entries gone are dropped first, in one write: a registry that cannot be written stops the command before any version is removed. The registry is read again just before the removals, for a project a sync registered meanwhile. An empty registry exits 2: pruning would remove every version. `CORTEX_NO_REGISTER` does not change it.

| Option | What it does |
|---|---|
| `--keep VERSION` | a version to keep, which no project pins — one the runtime uses, say; repeatable |
| `--dry-run` | say what would be removed, remove nothing, and drop no registry entry |

A project whose version was removed gets it back at its next `cortex sync`, checked against its `cortex.lock`.

### `cortex project`

```text
cortex project add [PATH] [--name NAME]
cortex project list [--json]
cortex project remove NAME|PATH
cortex project check
```

Manages the registry, `projects.toml` in the Cortex home — `~/.cortex/projects.toml` by default: this machine's Cortex projects, one entry per project root — the directory of `cortex.toml`, the workspace root in workspace mode — with a name and a path, and nothing else. What a project pins, and what it reads now, are read from the project each time. `cortex init` and `cortex sync` fill it on their own — `cortex upgrade` too, which syncs; `cortex prune` with no `DIR` and `cortex upgrade --all` read it ([ADR-011](docs/adr/ADR-011-project-registry.md)).

| Subcommand | What it does |
|---|---|
| `add [PATH] [--name NAME]` | registers the project at `PATH` — by default the current one, the nearest `cortex.toml`. Its name is `--name`, or the root's directory name, lowered, any other character turned to `-`, and `-2`, `-3`… added when the name is taken. A directory with no `cortex.toml` is refused; a path already listed is said, with its name, and nothing changes; a `--name` another path holds is refused |
| `list [--json]` | each entry, in name order: its name, its state, the version it pins, the version this machine reads now, its layout — from the bootstrap file's marker — and its path. The state is `ok`; `missing`, no directory there; `no-project`, a directory without `cortex.toml`; or `unreadable` — its directory or its files cannot be read, or this cortex refuses them — with why. `--json` prints the same as one JSON object, `{"format": 1, "projects": [...]}`, each project an object of `name`, `path`, `state`, `pinned`, `reads`, `layout` and `why`, with `null` for what is not known, for scripts and tools; its keys only grow within one `format` |
| `remove NAME\|PATH` | drops an entry, by name or by path. It never touches the project's files |
| `check` | says each entry that is not `ok`, and exits 1 if there is one. It removes nothing, and names `cortex project remove` and `cortex prune`, which do |

No registry is an empty list: `list` says so, and `check` exits 0. A project moved on disk is registered again at its new path, under a free name, by its next sync; its old entry is then `missing`, and the next `cortex prune` or `cortex project remove` drops it.

### `cortex validate`

```text
cortex validate [--service PATH] [--strict]
```

Checks the project's overlays against the spec it is synced to ([ADR-001](docs/adr/ADR-001-layered-overrides.md)): a header missing or malformed, a `Base:` that names no file, an overlay whose path does not mirror its base, an override of `characters.md`, `replacement` outside `workflows/`. It checks the spec against `cortex.lock` too — no lock, a lock of another version, a digest that differs — as warnings. A project not synced, or synced for another version than `cortex.toml`'s, is not validated: run `cortex sync`. In a checkout of Cortex, it validates the checkout as its own base.

| Option | What it does |
|---|---|
| `--service PATH` | validate the overlays of one service folder only — relative to the project root, or absolute |
| `--strict` | warnings fail too — what CI runs |

It exits `0` when clean, `1` on errors — or on warnings under `--strict` — and `2` on bad arguments, or with no spec to check against.

### Environment variables

| Variable | Read by | What it does |
|---|---|---|
| `CORTEX_HOME` | the binary, the install scripts | where Cortex lives on this machine — the binary in `bin/`, the store in `versions/`, the registry `projects.toml`: `~/.cortex` by default, `%USERPROFILE%\.cortex` on Windows |
| `CORTEX_RELEASES_URL` | the binary, the install scripts | where releases are downloaded from — a version's spec, `cortex self-update`'s binary, the install: GitHub's releases of `xod-labs/cortex` by default. A mirror serves the same layout, `latest/download/ASSET` and `download/VERSION/ASSET`, over https — http only to this machine, for tests. `cortex self-update` names it in a note before it downloads |
| `CORTEX_NO_UPDATE_CHECK` | `cortex upgrade` | set to anything but an empty value, `cortex upgrade` with no `VERSION` does not ask which release is the latest — for a CI, or a network that drops what it does not allow |
| `CORTEX_NO_REGISTER` | `cortex init`, `cortex sync`, `cortex upgrade` | set to anything but an empty value, they neither read nor write the registry to register the project — for a CI runner, where `cortex sync --locked` registers as any sync does, or a throwaway project. `cortex project`, `cortex prune` with no `DIR` and `cortex upgrade --all` still read the registry: it is their work |
| `CORTEX_NO_MODIFY_PATH` | `install.ps1` | `1` leaves your user `PATH` as it is, as `-NoModifyPath` does |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | the binary | the certificates a download is checked with, as OpenSSL reads them. Unset, the binary looks where the common systems keep theirs |
| `CORTEX_SOURCE_VERSION` | a source checkout | the version a checkout's tests stand for — for contributors; a built binary never reads it |

The runtime has its own — `CORTEX_BACKEND`, `CORTEX_DB`, `CORTEX_AUTH`… — in [runtime/README.md](runtime/README.md).

## 🧭 How-tos

### Move a project to a new version

```bash
cortex upgrade --dry-run        # what changes, layer by layer, and whether your overlays still hold
cortex upgrade                  # to the binary's own version — or cortex upgrade X.Y.Z
git add cortex.toml cortex.lock CLAUDE.md && git commit   # the files the command lists
```

A version newer than the binary is refused, with the `cortex self-update` command that installs it: run it first. If an overlay fails against the target — most often a base file renamed upstream, which breaks a `Base:` header — fix its header, or delete it if it is no longer needed: the release notes in [`changelog/`](changelog/) list breaking renames. `--branch NAME` and `--commit --message MESSAGE` create the branch and the commit for you. The [upgrade workflow](docs/extending-layers.md#-upgrade-workflow) has the details.

### Follow a teammate's bump

A pull brings a `cortex.toml` that pins another version. Run `cortex sync` — or let the assistant: at its next conversation it finds a spec of another version than the pin, says so, and runs `cortex sync` itself. When the new version is newer than your binary, sync refuses and names `cortex self-update X.Y.Z`; run it — the assistant does too — then `cortex sync` again. Until then, `cortex validate` refuses to check a spec synced for another version than the pin.

### Update the binary

```bash
cortex self-update              # the latest release
cortex self-update X.Y.Z        # a given one — an older one is a downgrade, and said
cortex --version
```

A binary before 1.4.0 has no `self-update`: run the install script again. Updating the binary moves no project — each stays on its pin until someone runs `cortex upgrade` in it — and a binary serves every version up to its own.

### Tidy the store

Every version a project ever synced stays in `~/.cortex/versions/`. To remove the ones no project uses any more:

```bash
cortex prune --dry-run          # what would go: the projects of the registry keep theirs
cortex prune
cortex prune ~/code ~/work      # the projects under these directories instead — the registry left alone
cortex prune --keep X.Y.Z       # and a version no project pins — the runtime's, say
```

A project nobody listed gets its version back at its next `cortex sync`, checked against its lock — its assistant runs it when it finds the spec gone. A project this machine has not synced since 1.5.0 is not registered: sync it, or `cortex project add` it, before the first `cortex prune` with no directory.

### Keep several projects in step

```bash
cortex project list                                          # where each project stands
cortex upgrade --all --dry-run                               # what each one would get
cortex upgrade --all --branch cortex-upgrade --commit --message "Move to Cortex X.Y.Z"
```

Each project moves on its own: one that refuses does not stop the others, and the command ends with a line per project and what each one must commit. Without `--commit` nothing is committed; with it, only the files the upgrade wrote, and none where you had changes of your own in them. Fix a project that refused, and run the same command again: the projects done have nothing more to write, and the one fixed is written on its branch. Nothing is pushed: open the pull requests as your team does.

### Run Cortex in CI

The job installs the binary of the version `cortex.toml` pins — its checks are that version's — then runs `cortex sync --locked`, which never writes `cortex.lock`: a lock missing, or naming another version than `cortex.toml`, fails the job instead of being written where nobody commits it. Then `cortex validate --strict`. `--locked` is from Cortex 1.4.0, and the lines leave it out for a project pinned before it:

```bash
version="$(LC_ALL=C tr -d '\357\273\277' < cortex.toml | sed -n "s/^[[:space:]]*version[[:space:]]*=[[:space:]]*[\"']\([^\"']*\)[\"'].*/\\1/p" | head -n 1)"
[ -n "$version" ] || { echo "cortex.toml pins no version" >&2; exit 1; }
curl -fsSL https://raw.githubusercontent.com/xod-labs/cortex/main/install.sh | sh -s -- "$version"
export PATH="$HOME/.cortex/bin:$PATH"
case "$version" in 0.* | 1.[0-3].*) locked= ;; *) locked=--locked ;; esac    # --locked from 1.4.0
cortex sync $locked
cortex validate --strict
```

The PowerShell lines for a Windows runner are in [extending-layers.md](docs/extending-layers.md#-validating-your-overlays). On a runner that outlives its jobs, set `CORTEX_NO_REGISTER=1` so that its registry does not collect every checkout.

### Add an overlay

Teach an agent your project's conventions without forking Cortex: mirror the base file's path under `agents/` — or `{service}/agents/` for one service — start the file with an `OVERLAY` header whose `Base:` names the base file, `cortex/agents/roles/engineering/lead-backend.md` say, tag its sections `(additive)`, and run `cortex validate`. [Getting Started](docs/getting-started.md#override-a-base-layer-for-your-project-the-cascade) has a short example; [Extending layers](docs/extending-layers.md) the header, the four layers, what cannot be overridden, and more examples; [Creating a theme](docs/creating-a-theme.md), a personality of your own.

### What the assistant does on its own

The bootstrap file has the assistant keep the machine in step with the project before anything else ([ADR-010](docs/adr/ADR-010-lock-and-upgrade.md) §3.8):

| The assistant finds | It |
|---|---|
| no `cortex.local.toml`, or no `spec` in it | runs `cortex sync` at the project root — the workspace root in workspace mode |
| a `spec` naming a directory that does not exist — `cortex prune` removed the version, or the store was emptied | runs `cortex sync`, which gets the version back |
| a spec of another version than `cortex.toml`'s — a teammate moved it | runs `cortex sync`, and goes no further until it succeeded |
| `cortex sync` saying this cortex is too old for the project — a newer pin, or a later lock format | runs the command the refusal names — `cortex self-update X.Y.Z`, or the install script's from a binary before 1.4.0 — then `cortex sync` again |
| no `cortex` command, nor `cortex-ai`, on `PATH` or in `~/.cortex/bin` | tells you to install it, as [Installation](#-installation) says, and goes no further |

- **Which command.** `cortex-ai` when there is one; otherwise `cortex`, once `cortex --version` printed `cortex` followed by a version — anything else is another program, whose `sync` it must not run. It looks in `~/.cortex/bin` (`%USERPROFILE%\.cortex\bin`) too when its tool's `PATH` has neither: an editor started before the install changed `PATH`.
- **It says what it runs, and why, before it runs it.** Whether you approve each command is your tool's setting, not the bootstrap's: a tool set to ask, asks. An assistant that cannot run commands asks you to run them.
- **It never moves the project's pin of its own accord** — no `cortex upgrade`, no `cortex upgrade --all`, no edit of `version` in `cortex.toml`: moving the project is the team's decision, made in a commit. Ask it, and it does — the dry run first.

## ⚙️ The Runtime

[`cortex-runtime`](runtime/README.md) is the **deployable engine**. It consumes [`cortex-core`](core/README.md) — the one implementation of the ADR-001 cascade in code ([ADR-007](docs/adr/ADR-007-cortex-core.md)) — and wraps it in a thin, domain-agnostic HTTP API that drives an agentic loop — so the same spec you use at design time can run as a 24/7 service ([ADR-002](docs/adr/ADR-002-cortex-runtime.md)).

Everything below the API is **swappable and opt-in** — you run only what you need:

| Capability | What it does | ADR | How to enable |
|---|---|---|---|
| **Agnostic API** | `POST /run` (+ project aliases), `/resolve`, `/reply`, `/webhook/{source}`; read-only monitoring `GET /runs`; `/health` + `/ready` | [002](docs/adr/ADR-002-cortex-runtime.md) | always on |
| **Model backends** | `demo` (no key/CLI), `claude-cli` (Pro/Max subscription), `anthropic-api` (Console key) | 002 | `CORTEX_BACKEND` |
| **Persistence** | `StateStore` for conversation state, run history & metrics, audit log — InMemory / SQLite / PostgreSQL | [003](docs/adr/ADR-003-persistence-state-layer.md) | `CORTEX_DB` / `CORTEX_DATABASE_URL` |
| **Security perimeter** | Bearer + HMAC auth, tenant registry, per-tenant budget caps, rate-limiting, admin CLI | [004](docs/adr/ADR-004-api-security.md) | `CORTEX_AUTH=on` |
| **Async execution** | Job queue, `POST /run` → `202` + poll, readiness `/ready`, graceful drain | [005](docs/adr/ADR-005-execution-model-resilience.md) | `CORTEX_ASYNC=on` |

**Quick start** (no key, no CLI — runs the whole wire):

```bash
cd runtime && pip install -e ../core -e ".[serve]"   # cortex-core first: the runtime depends on it
CORTEX_BACKEND=demo python -m cortex_runtime      # serves on 127.0.0.1:8000
```

Or bring up the container stack (runtime + optional Postgres/DBHub, behind Traefik):

```bash
cd deploy && docker compose up -d                 # see deploy/README.md
```

A project with a `cortex.toml` gives the runtime its base through the store: [the migration guide](docs/migrating-to-the-binary.md#6-the-runtime) says how. Full engine docs live in [runtime/README.md](runtime/README.md); deployment & security setup in [deploy/README.md](deploy/README.md); the HTTP contract in [docs/api/](docs/api/) (OpenAPI + Postman).

## 📚 Documentation

**Using Cortex**
- [**Getting Started**](docs/getting-started.md) — step-by-step installation guide (single project & workspace)
- [**Extending layers**](docs/extending-layers.md) — overlay your project's rules onto roles, capabilities, personalities, and workflows; validation and the upgrade workflow
- [**Creating a theme**](docs/creating-a-theme.md) — customize the tone and style of agents
- [**Moving to the `cortex` binary**](docs/migrating-to-the-binary.md) — from a git submodule or clone of Cortex, and which sync mode each AI tool needs
- [**Moving to xod-labs**](docs/moving-to-xod-labs.md) — Cortex moved from `adorey/cortex` on 2026-10-30: what to update

**The runtime (the engine)**
- [**cortex-runtime**](runtime/README.md) — the deployable engine: API, agentic loop, backends, state, security, async
- [**Deployment**](deploy/README.md) — Docker/compose stack, Traefik, security setup (admin CLI, HMAC secrets, token minting)
- [**API reference**](docs/api/) — OpenAPI spec + Postman collection
- [**Claude CLI setup**](runtime/docs/claude-cli-setup.md) — run the runtime against a Pro/Max subscription

**Design & contribution**
- [**Architecture Decision Records**](docs/adr/) — the *why* behind the framework, the `cortex` command and the runtime
- [**Contributing**](CONTRIBUTING.md) — how to add roles, capabilities, themes, workflows, or fix bugs; the test loop and the release cycle
- [**Repository layout**](docs/repository-layout.md) — what sits where in this repository
- [**The roadmap**](https://github.com/xod-labs/cortex/issues?q=is%3Aissue%20label%3Aadr-epic) — the `adr-epic` issues, one per decision from proposal to release; [the issues](https://github.com/xod-labs/cortex/issues) for bugs and ideas

## 📋 Changelog

The full, versioned history lives in **[`CHANGELOG.md`](CHANGELOG.md)** (Keep a Changelog format) — the single source of truth for versions. Each entry links to its detailed, narrated release note in [`changelog/`](changelog/).

## 🎯 Philosophy

**The spec**
- **Zero project dependency**: roles are stack-agnostic, the stack lives in `project-context.md`
- **Plug & Play**: one install per machine, `cortex init` per project — single project mode or multi-project workspace
- **Composable**: role + capabilities + personality + context + workflow = complete agent
- **Two context files**: `project-overview.md` (vision & business) + `project-context.md` (stack & conventions) — separated to never mix the WHAT and the HOW
- **Loadable capabilities**: `capabilities/` cards are reusable across projects, automatically loaded by the PM based on the active role and project stack
- **Multi-project**: workspace mode with `@alias` per service — Cortex detects the active service from the prompt or open files
- **Layered overrides**: every layer can be extended at workspace or service level via overlays — host projects teach Cortex their conventions without forking ([ADR-001](docs/adr/ADR-001-layered-overrides.md))
- **Pinned, locked, moved on purpose**: a project uses the version it pins, checked against its lock; the machine follows the project, and the project moves only when the team commits it

**The runtime**
- **The firewall**: the runtime consumes the spec; the spec never depends on the runtime — the Markdown cascade stays portable ([ADR-002](docs/adr/ADR-002-cortex-runtime.md))
- **Domain-agnostic**: the engine keys everything on an opaque `subject` — it never knows it is dealing with a "ticket", a dashboard, or a cron key
- **Swappable everything**: model backend, state store, and secret provider all sit behind interfaces — from a keyless `demo` to Postgres + Anthropic API in production
- **Opt-in hardening**: security and async are off by default and switch on per environment — dev stays frictionless, prod gets the perimeter

**Both**
- **Append-only ADRs**: significant design decisions are documented and traceable in [docs/adr/](docs/adr/)
- **Scalable**: add your own roles, capabilities, themes, workflows, services — or backends behind the runtime interfaces

> *"Documentation is like the developer's tea: nobody wants it until they desperately need it."* — Arthur Dent
