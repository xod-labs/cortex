# Getting Started — Cortex

> *"Don't Panic. And read this guide before doing anything else."* — Arthur Dent

This step-by-step guide covers installing the **design-time spec** in both modes: **single project** and **multi-service workspace**.

> Looking to run the engine instead? The deployable runtime (API, agentic loop, deployment) has its own guides: [runtime/README.md](../runtime/README.md) and [deploy/README.md](../deploy/README.md).

---

## Prerequisites

- An AI coding tool: GitHub Copilot, Cursor, Claude Code, OpenAI Codex, or any tool that supports a custom system instructions file
- A machine the `cortex` command is built for: Linux x86_64 or aarch64 (glibc 2.28 or later), macOS on Apple silicon, or Windows x86_64
- Nothing else: using Cortex needs no Git, Bash or Python on the host. Git is only for your project itself.

> **Your project already has Cortex as a git submodule or a clone?** See [Moving to the `cortex` binary](migrating-to-the-binary.md) first.

---

## Install the `cortex` command (once per machine)

```bash
# Linux, macOS
curl -fsSL https://raw.githubusercontent.com/xod-labs/cortex/main/install.sh | sh

# A given version
curl -fsSL https://raw.githubusercontent.com/xod-labs/cortex/main/install.sh | sh -s -- 1.0.0
```

```powershell
# Windows PowerShell
irm https://raw.githubusercontent.com/xod-labs/cortex/main/install.ps1 | iex
```

- The binary goes into `~/.cortex/bin` (`%USERPROFILE%\.cortex\bin` on Windows). `install.ps1` adds that directory to your user `PATH`; `install.sh` prints the line to add to your shell profile.
- When another `cortex` command comes first on `PATH`, the scripts install it as `cortex-ai` instead: use that name in the commands below, or force `cortex` with `--name cortex` (`-Name cortex` for `install.ps1`).
- `cortex self-update` replaces the binary with the latest release's, checked against its `SHA256SUMS` — `cortex self-update X.Y.Z` with that version's. Running a script again works too, and is the only way for a binary before 1.4.0. It replaces the file it runs from, where it is: a binary kept in a directory named after its version — `/opt/cortex-1.4.0/cortex-ai`, say — gets another version under that name. `cortex --version` prints the installed version.

The spec itself lives in the **store**, `~/.cortex/versions/X.Y.Z`: one read-only directory per version, filled on demand — the binary's own version from itself, any other downloaded once and checked against its release's `SHA256SUMS`. `$CORTEX_HOME` moves `~/.cortex` elsewhere.

---

## 🚀 Mode 1 — Single project (one git repo)

### Step 1 — Initialise the project

```bash
cd my-project/

# Default: H2G2 theme, GitHub Copilot
cortex init

# Target a specific AI tool
cortex init --tool copilot   # → .github/copilot-instructions.md (default)
cortex init --tool cursor    # → .cursor/rules/cortex.mdc
cortex init --tool claude    # → CLAUDE.md
cortex init --tool agents    # → AGENTS.md (Codex, etc.)
cortex init --tool custom --instructions-file path/to/file

# Without personality (neutral professional agents)
cortex init --no-personality
```

`cortex init DIR` sets up `DIR` instead of the current directory. `--theme` picks another theme (`h2g2` by default): one the pinned Cortex version ships, or one of your own in `agents/personalities/` — see [Create a personality theme](#create-a-personality-theme).

The command creates:
- `cortex.toml` — **committed**: the Cortex `version` the project uses (the binary's own) and the team's `theme`
- `cortex.local.toml` — **ignored by git** (the command adds it to `.gitignore`): `spec`, where the spec is on this machine, written by the `cortex sync` it runs
- The instructions file for your AI tool (path depends on `--tool`) — an existing one is kept unless `--force`, which keeps it as `FILE.bak`
- `project-overview.md` — to fill in: vision, stakeholders, business flows
- `project-context.md` — to fill in: stack, conventions, tools

The two context files are written only when missing. Running `cortex init` again keeps the instructions file, and `cortex.toml` with its version: it changes only the keys an option names — `--theme` or `--no-personality`, `--link` or `--copy`, `--claude-access`. `--force` replaces the instructions file, and only it.

The instructions file is yours to adapt — retitle it, add sections — but **keep its marker line as it is**, `<!-- cortex:bootstrap layout=single format=1 digest=… -->` under the title: `cortex init` reads from that line the layout and the format the file was written for, and `cortex upgrade`, from its digest, whether anyone changed the file since — one nobody changed is written again when the version moves.

### Step 2 — Commit, then `cortex sync` after every clone

Commit `cortex.toml`, `cortex.lock`, the instructions file and the two context files. Each developer who clones the project then runs, once, at its root:

```bash
cortex sync
```

— as they would `npm install`. It puts the pinned version in the store if it is not there yet, checks it against `cortex.lock`, and writes `spec` in their `cortex.local.toml`. Until a developer has run it, the instructions have the AI tool run it — saying so, and asking first where the tool is set to ask — and go no further until it succeeded.

`cortex.lock` holds the version and the digest of its spec, one SHA-256 over its files: a spec whose files differ from the ones the team locked is put in the store again, and refused if it still differs. In CI, run `cortex sync --locked`: it never writes the lock, and fails when `cortex.lock` is missing or names another version than `cortex.toml` — a lock nobody committed fails the job instead of being written on the runner.

**Where the spec is.** In the instructions, in these docs and in every overlay's `Base:` header, `cortex/` is the directory that `spec` names — a name, not necessarily a directory of the project. `cortex sync` has three modes, chosen by `--store`, `--link` or `--copy`:

| Mode | In the project | `spec` |
|---|---|---|
| `store` (default) | nothing — the tool reads the spec in place | the store's path, `~/.cortex/versions/X.Y.Z` |
| `link` | `cortex/`, a link to the store — a junction on Windows | `cortex` |
| `copy` | `cortex/`, a read-only copy | `cortex` |

`sync = "link"` or `sync = "copy"` in `cortex.toml` makes a mode the team's default — `cortex init --link` or `--copy` writes it, and adds `/cortex` and `/.cortex-sync-*` to `.gitignore` — no final slash: a link is a file to git; a flag overrides it for one run. `cortex sync --from PATH` uses a checkout of Cortex instead of the store, with no version check: it is for contributors testing an unreleased spec.

Which mode a tool needs is measured, not assumed — the table is in [Moving to the `cortex` binary](migrating-to-the-binary.md). So far only Claude Code is measured: in `store` mode it asks for permission to read the store once per session, and `copy` needs nothing. With `claude_access = true` in `cortex.toml` — `cortex init --tool claude --claude-access`, or the question `cortex init` asks on a terminal — `cortex sync` writes the store's path in `.claude/settings.local.json`, and Claude Code reads it without asking. `cortex sync --claude-access` does the same for you alone.

### Step 3 — Fill in the context files

**`project-overview.md`** (the WHAT and WHY):
```markdown
@alias: my-project

## 🎯 Mission
B2B contract management platform — SaaS, SME market.

## 👥 Stakeholders
- Company admin, Manager, Employee

## 🔄 Main flows
- Contract creation / validation / signing
- Role-based permission management

## ⚠️ Constraints
- GDPR, data hosted in EU
- SLA 99.9%
```

**`project-context.md`** (the HOW and WHERE):
```markdown
@alias: my-project

## 🛠️ Tech stack
- PHP 8.3 / Symfony 7.2
- MySQL 8.0
- Docker + Kubernetes

## 📐 Conventions
- PSR-12, DDD, REST API
- PHPUnit tests, minimum 80% coverage
```

### Step 4 — First interaction

In your AI tool, simply mention the desired agent in your prompt.

**With the H2G2 theme:**
```
@Oolon I want to add a pagination system on the /contracts API
```

**Without a theme (direct role):**
```
@prompt-manager I want to add a pagination system on the /contracts API
```

### Step 5 — How dispatch works

When you call `@Oolon` (or `@prompt-manager`):

1. **Analysis** — Oolon reformulates and clarifies your request
2. **Workflow** — Searches for a matching workflow in `agents/workflows/` (generic) then in your `agents/workflows/` (project)
3. **Dispatch** — Identifies the expert: *"Handing over to @Hactar (Lead Backend)"*
4. **Capabilities** — Loads the `capabilities/` files matching your stack (e.g. `php.md`, `symfony.md`, `mysql.md`)
5. **Delivery** — The expert responds with your project's full context

---

## 🏢 Mode 2 — Multi-service workspace

For a workspace containing multiple services. **Two flavors** depending on your repo layout — Cortex sets both up the same way, with `cortex.toml` at the workspace root and no copy of Cortex next to the services.

### Flavor 2.A — Monorepo (one git repo, services as subfolders)

```
workspace/                ← single git repo
├── cortex.toml           ← committed with the monorepo
├── api-backend/          ← subfolder of the monorepo
├── front-web/
└── notif-service/
```

### Flavor 2.B — Multi-repo workspace (each service is its own git repo)

```
workspace/                ← just a folder, NOT a git repo (or a separate one)
├── cortex.toml
├── api-backend/          ← independent git repo
├── front-web/            ← independent git repo
└── notif-service/        ← independent git repo
```

The steps are identical for both flavors.

### Step 1 — Run `cortex init --workspace` at the workspace root

```bash
cd workspace/      # the monorepo root, or the folder holding your service clones
cortex init --workspace --service api-backend --service front-web --service notif-service
```

Each `--service` is a folder inside the workspace; its `@alias` is the folder's name (`--service core/web` → `@web`). On a terminal, with no `--service`, the command asks for them:

```
   Enter the names of the services to create (empty entry to stop):
   Service name (e.g. api-backend, front-web): api-backend
   Service name (e.g. api-backend, front-web): front-web
   Service name (e.g. api-backend, front-web):     ← empty entry to finish
```

Combine with `--tool` to target your AI tool:
```bash
cortex init --workspace --tool cursor --service api-backend --service front-web
```

### Step 2 — What it creates

For each service:
- `{service}/project-overview.md` — with its `@alias` pre-filled
- `{service}/project-context.md` — with its `@alias` pre-filled

At the workspace root, as in single project mode: `cortex.toml`, `cortex.local.toml` (git-ignored) and the instructions file of your `--tool` — written from the workspace bootstrap, `cortex/templates/bootstrap-instructions-workspace.md`. Its marker line is `<!-- cortex:bootstrap layout=workspace format=1 digest=… -->`: keep it as it is when you adapt the file, as in single project mode.

And at the workspace root (optional) — this is your **developer** tier, not shared by default:
- `project-overview.md` — your own notes on the global vision (shared stakeholders, common constraints)
- `project-context.md` — your own notes on shared conventions (linting, CI/CD, versioning)

If your team wants to formalize and version this instead of leaving it per-developer, turn `agents/` into its own git repository and add the same two files there. `agents/project-overview.md` / `agents/project-context.md` become the **team** tier — read in *addition to* the developer tier above, not instead of it (both are optional, both get read if present). If `agents/` is already its own git repository when you run `cortex init --workspace`, the command scaffolds the team pair there. See [ADR-006](adr/ADR-006-workspace-shareable-repo.md).

### Step 3 — Commit, then `cortex sync` after every clone

As in [single project mode](#step-2--commit-then-cortex-sync-after-every-clone): commit `cortex.toml`, `cortex.lock` and the instructions file with the workspace root, and each developer runs `cortex sync` at the workspace root after a clone. When the root is no git repository at all (flavor 2.B without a repository of its own), there is nothing to clone: each developer runs `cortex init --workspace` there instead.

### Step 4 — Fill in the context per service

Each service has its own context files. The `@alias` targets a specific service:

**`api-backend/project-overview.md`**:
```markdown
<!-- @alias: api-backend -->

## 🎯 Mission
REST API — contract management, JWT authentication
```

**`front-web/project-context.md`**:
```markdown
<!-- @alias: front-web -->

## 🛠️ Stack
- TypeScript / React 18
- Vite, TailwindCSS
```

### Step 5 — Target a service in your prompts

```
@api-backend Add a pagination endpoint on /contracts
@front-web   Create a table component with sorting and filters
```

If you don't use an alias, Cortex infers the service from the active file context.

---

## ➕ Going further

### Override a base layer for your project (the cascade)

You don't need to fork Cortex to teach an agent your project's conventions. Every layer (`roles/`, `capabilities/`, `personalities/`, `workflows/`) supports **overlays** at workspace and/or service level.

Quick example — teach `@Hactar` (Lead Backend) your project's namespace rule:

```bash
# 1. Mirror the path of the base file
mkdir -p agents/roles/engineering/
cat > agents/roles/engineering/lead-backend.md <<'EOF'
<!-- OVERLAY
     Base: cortex/agents/roles/engineering/lead-backend.md
     Scope: workspace
     Semantic: additive
-->

# Lead Backend — Overlay (project conventions)

## 📏 Project rules (additive)
- Namespace `Waste\` (not `App\Waste\`)
- `bin/linters fix && bin/linters lint` mandatory before commit
EOF

# 2. Validate (recommended)
cortex validate
```

The Prompt Manager loads `cortex/agents/roles/engineering/lead-backend.md` first — from the directory `spec` names, whatever the sync mode — then layers your overlay on top.

`cortex validate` checks every overlay against the spec the project is synced to; `--service PATH` checks one service only. It exits `0` when clean, `1` on errors, `2` when it cannot run or the arguments are wrong. With `--strict`, warnings fail too — what CI should run, after installing the binary and running `cortex sync --locked`.

📖 **Full guide:** [extending-layers.md](extending-layers.md) — covers all four layers, examples, edge cases, and validation.

### Create a project workflow

Workflows are a special case of overlay (semantic: replacement).

```bash
mkdir -p agents/workflows/engineering/
# cortex/templates/ is in the spec — the store by default; 1.0.0 stands for cortex.toml's version
cp ~/.cortex/versions/1.0.0/templates/workflow.md.template agents/workflows/engineering/my-workflow.md
chmod u+w agents/workflows/engineering/my-workflow.md   # the spec is read-only, and so is cp's copy
# Fill in the template (don't forget the OVERLAY header)
```

In `link` or `copy` mode the template is at `cortex/templates/workflow.md.template` in the project.

### List your machine's projects — `cortex project`

A machine keeps the list of its Cortex projects in `projects.toml`, beside the store: `~/.cortex/projects.toml`, or `$CORTEX_HOME/projects.toml` ([ADR-011](adr/ADR-011-project-registry.md)). An entry is a project root — the directory of `cortex.toml`, the workspace root in workspace mode — with a name and a path, nothing else: what a project pins and reads is read from the project each time.

`cortex init` and `cortex sync` fill it — and `cortex upgrade`, which syncs. Once one of them has succeeded, a project the registry does not list is added, named after its directory, and said once: *registered as acme — `cortex project list` lists this machine's projects*. An entry is never renamed or moved: a project moved on disk is registered again at its new path, under a free name, and its old entry is `missing` until `cortex project remove` drops it. A registry that cannot be read or written is a warning, never a failed sync. On a CI runner — `cortex sync --locked` registers as any sync does — or for a throwaway project, set `CORTEX_NO_REGISTER` to any value but an empty one: the three commands then neither read nor write the registry. `cortex project` still does: managing it is its work. So does `cortex upgrade --all` read it, the list it moves.

`cortex project` manages it by hand:

```bash
cortex project add                          # the project of the current directory, named after its directory
cortex project add ~/code/acme --name acme  # another one, under a name of your choice
cortex project list                         # where each one stands
cortex project list --json                  # the same, as one JSON object
cortex project remove acme                  # by name or by path — the project's files are not touched
cortex project check                        # exits 1 when one is not ok
```

`list` gives, in name order, each project's name, its state, the version its `cortex.toml` pins, the version this machine reads now — the `spec` of its `cortex.local.toml` — its layout, from the marker of its instructions file, and its path. A project is `ok`, `missing` — no directory at its path — `no-project` — a directory without `cortex.toml` — or `unreadable`, with why. `--json` gives the same as one object, `{"format": 1, "projects": [...]}`, each project an object of `name`, `path`, `state`, `pinned`, `reads`, `layout` and `why`, with `null` for what is not known; the keys only grow within one `format`. `check` removes nothing: `cortex project remove` drops an entry, and `cortex prune` the entries whose project is gone.

The commands write the file, whole and one at a time. Edit it by hand with care: `cortex` refuses a file with a key it does not know, a name other than lowercase letters, digits, `.`, `_` and `-`, a path holding a control character — of C0, from NUL to the escape, or DEL — or two entries naming the same directory. A file it refuses, every command that reads it refuses — `cortex project remove` too: fix it by hand, the entry named in the message corrected or removed.

### Free the store — `cortex prune`

The store keeps every version a project of the machine synced. `cortex prune` removes those no project uses any more ([ADR-010](adr/ADR-010-lock-and-upgrade.md) §3.6, [ADR-011](adr/ADR-011-project-registry.md) §3.4):

```bash
cortex prune --dry-run          # what it would remove, and the entries it would drop: nothing changes
cortex prune                    # keeps the versions of the projects registered — `cortex project list`
cortex prune ~/code ~/work      # of the projects under these directories instead: the registry is not read
cortex prune --keep 1.3.0       # a version no project pins, kept — one the runtime uses, say
```

It keeps the version each project pins and the one it reads now — a teammate's bump pulled and not synced yet leaves it on the version before — the binary's own version, and each `--keep`. With no directory, the projects are the registry's:

- an entry whose project is gone — `missing`, on a drive that is not mounted too, or `no-project` with nothing of Cortex left — is dropped from the registry, and said, and its version is no longer kept for it. Its next `cortex sync` registers it again;
- a `no-project` entry whose `cortex.local.toml` still reads a version — a branch without Cortex checked out, a rebase under way — stays, and keeps that version: `cortex project remove` drops it, if the project really left Cortex;
- an `unreadable` entry is named, and stays: the versions that can be read of it are kept — none, when not even its pin can be read;
- with nothing registered it exits 2, rather than remove every version: register the projects, or give their directories.

A project this machine never synced since 1.5.0 is not registered, and loses its version: sync it, or `cortex project add` it, before the first `cortex prune` with no directory. Its assistant notices a spec that is gone and runs `cortex sync`, which gets the version back. `CORTEX_NO_REGISTER` does not change `prune`: it keeps the commands that register from the registry, and `prune` with no directory reads it, and drops from it, whatever its value.

A project it did not see — not registered, or under none of the directories — gets its version back at its next `cortex sync`, checked against its `cortex.lock`.

### Add a capability (contributing to cortex)

Capabilities live in `cortex/agents/capabilities/`. To add a *new* capability to the framework (PR upstream), work in a clone of the Cortex repository — the spec in the store is read-only — and test it in a host project with `cortex sync --from PATH`:

```
cortex/agents/capabilities/
├── languages/       php.md, typescript.md
├── frameworks/      symfony.md, vue.md, starlight.md
├── infrastructure/  docker.md, kubernetes.md
├── databases/       mysql.md, postgresql.md, mongodb.md
├── search/          opensearch.md
├── testing/         component-testing.md, e2e-testing.md
├── practices/       code-comments.md
└── security/        owasp.md
```

Copy the format of an existing capability, then declare it in the `🔌 Capabilities` section of the relevant role. See [CONTRIBUTING.md](../CONTRIBUTING.md).

For project-specific capability flavor (your conventions, your tooling) → use an overlay, not a contribution.

### Create a personality theme

See [`docs/creating-a-theme.md`](creating-a-theme.md) for the full guide. Then make it the team's theme in `cortex.toml`, or only yours in `cortex.local.toml`, which git ignores and which wins over the team's:

```toml
theme = "my-theme"
```

The PM picks it up on the next conversation. `cortex sync` warns when the active theme is in neither the spec nor `agents/personalities/`.

> Theme-level reassignment (changing which character plays which role) is **not** done via overlay — it requires a full theme fork. Per-character flavor (extra quotes, project-specific behavior) **is** an overlay. See [extending-layers.md](extending-layers.md).

### Add a role (contributing to cortex)

1. Create `cortex/agents/roles/{category}/my-role.md` following the existing format
2. Add a `🔌 Capabilities` section if it's a technical role
3. If a theme is active: add the character to `characters.md` and create their `.md` card
4. Open a PR — see [CONTRIBUTING.md](../CONTRIBUTING.md)

---

## 🗺️ Overview of the 5 layers

| Layer | Base location | Override locations | Answers |
|---|---|---|---|
| **Roles** | `cortex/agents/roles/{cat}/` | `{workspace_root}/agents/roles/`, `{service}/agents/roles/` | *WHAT* to do |
| **Capabilities** | `cortex/agents/capabilities/{cat}/` | `{workspace_root}/agents/capabilities/`, `{service}/agents/capabilities/` | *WHAT I KNOW HOW TO DO* |
| **Personalities** | `cortex/agents/personalities/{theme}/` | Same paths under `{workspace_root}/` and `{service}/` (except `characters.md`) | *WHO* you are |
| **Context** | `project-overview.md` + `project-context.md`, per-service, and at the workspace root as the **developer** tier | `{workspace_root}/agents/project-overview.md` + `project-context.md` — the **team** tier ([ADR-006](adr/ADR-006-workspace-shareable-repo.md)) | *WHERE / WHY* you work |
| **Workflows** | `cortex/agents/workflows/{cat}/` | `{workspace_root}/agents/workflows/`, `{service}/agents/workflows/` | *HOW and WITH WHOM* to orchestrate |

Override semantic: **additive** for roles/capabilities/personalities, **replacement** for workflows. Context is different again: **pure aggregation** — team and developer are different owners, not different specificities of one rule, so both are read and neither overrides the other (see ADR-006). See [extending-layers.md](extending-layers.md) for the roles/capabilities/personalities/workflows cascade.

---

## ❓ Quick FAQ

**Q: Do I need to use a personality theme?**
No. `cortex init --no-personality` gives you streamlined, professional, neutral agents — `theme = "none"` in `cortex.toml`.

**Q: I don't have a matching workflow. What happens?**
The PM dispatches directly to the right expert without a template. If the case recurs, it will suggest creating a workflow using the template.

**Q: Can I call an agent directly without going through the PM?**
Yes. `@Hactar` invokes the Lead Backend directly. But going through the PM guarantees prompt optimisation and capability loading.

**Q: Is Cortex updated automatically?**
The machine follows the project; the project moves only when the team moves it. A project stays on the `version` its `cortex.toml` pins:
- **The project:** run `cortex upgrade` — `--dry-run` first says what changes and checks your overlays. It writes `version` in `cortex.toml` and syncs. Each teammate syncs once they pull the change — their assistant runs `cortex sync` itself when it finds the spec of another version than `cortex.toml`'s, saying so, and `cortex validate` refuses a spec synced for another version. The assistant runs `cortex upgrade` only when you ask for it, the dry run first — never of its own accord: moving the pin is the team's decision.
- **Every project of the machine:** `cortex upgrade --all --dry-run`, then `cortex upgrade --all`, moves each project of the registry — `cortex project list` lists them — to the same version. A project that refuses stops none of the others, one that is not `ok` is skipped, and the command ends with a line per project and the files each must commit. Nothing is committed by default: `--branch NAME` makes the branch `NAME` in each git project with something to commit, before anything is written, and `--commit --message MESSAGE` commits there the files the upgrade wrote, and nothing else — nothing at all where `cortex.toml`, or a bootstrap file it wrote, held changes of yours. Nothing is pushed. Run again after fixing a project that refused, the same command refuses none it moved: a project with nothing to write gets no branch, and one already on `NAME` is written there. Interrupted, it still ends with a line per project, and exits 130. Both options work without `--all` too, for the one project.
- **The binary:** `cortex self-update`. A binary serves every version from 1.0.0 up to its own; a project pinned to a newer one is refused, with the `cortex self-update` command that installs it — which the assistant runs too, before it syncs. With no version given, `cortex upgrade` says when a release newer than the binary is out.

Check the [changelog](../changelog/) before updating in production.

**Q: Can I use Cortex without Git at all?**
Yes. The install script brings the binary, and `cortex sync` the spec: Cortex itself needs no Git. Without a repository, `cortex.toml` simply stays on your machine.

**Q: I have a multi-repo workspace where each service is its own git repo. Do I need a parent repo?**
No. Cortex doesn't care whether the workspace folder is a git repo. Run `cortex init --workspace` in it, and you're set — nothing of Cortex sits next to your services but `cortex.toml`, `cortex.local.toml` and the instructions file. This is [flavor 2.B](#flavor-2b--multi-repo-workspace-each-service-is-its-own-git-repo).

**Q: My project has Cortex as a git submodule or a clone. How do I move it to the binary?**
`cortex init` refuses while `cortex/` is a submodule or a clone, and prints the commands that remove it, without running them. [Moving to the `cortex` binary](migrating-to-the-binary.md) walks through the switch.
