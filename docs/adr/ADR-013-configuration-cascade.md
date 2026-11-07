# ADR-013 — The configuration cascade: the team's settings, the machine's, and where each value comes from

- **Status:** Proposed
- **Date:** 2026-11-07 (proposed, with the maintainer's arbitrations of 2026-11-06 and 2026-11-07, §2)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `core/cortex_core/` (a new module for the resolution; `project.py`, the grammar; `resolver.py`, `catalog.py`, `validate.py`, `workspace.py`, the team's folder), `cli/cortex_cli/` (`config`, `sync`, `upgrade`, `init`, `validate`, `registry`, `prune`, `store`, `self_update`, `claude`, `main`, and a new `cortex config`), `runtime/cortex_runtime/` (`base.py`, `context.py`, `run.py`, `__main__.py`), `templates/bootstrap-instructions*.md` (format 3), `agents/roles/prompt-manager.md` and `agents/workflows/README.md` (the team's folder), `README.md` (its CI recipe and its variables), `docs/getting-started.md`, `docs/extending-layers.md`, `docs/creating-a-theme.md`
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) §3.3 (`config.toml`, reserved), §3.4, §3.6, §7.1 (this follow-up) and §9 · [ADR-010](ADR-010-lock-and-upgrade.md) §3.1 to §3.6 and §3.8 · [ADR-011](ADR-011-project-registry.md) §3.2, §3.4 and §3.5 · [ADR-007](ADR-007-cortex-core.md) §3.1 · [ADR-006](ADR-006-workspace-shareable-repo.md) §3.2 · [ADR-001](ADR-001-layered-overrides.md) §3.1 · [ADR-012](ADR-012-bootstrap-block.md) · epic [#98](https://github.com/xod-labs/cortex/issues/98), first of its two records · [#327](https://github.com/xod-labs/cortex/issues/327) · [#137](https://github.com/xod-labs/cortex/issues/137) · [#40](https://github.com/xod-labs/cortex/issues/40)

---

## 1. Context

ADR-008 left the configuration cascade to #98 (§7.1): organisation policy ▸ project ▸ project-local ▸ machine, *of which `cortex.toml` and `cortex.local.toml` are the first two files*, and `~/.cortex/config.toml` reserved for the machine (§3.3). On 2026-11-06 the maintainer cut #98 in two: this record, then the IDE modes, which #137 will validate. Four gaps have shown since:

- **A team that does not commit its root cannot pin its version.** At a workspace root that is no git repository — flavor 2.B of ADR-006 — `cortex.toml` is committed nowhere, and pinning is per developer (ADR-008 §9). Moving Bluspark's workspace to 1.7.0 on 2026-11-07 moved one machine. ADR-008 §9 left the answer here: a pin in the team's `agents/` repository.
- **The machine's settings are variables only** — `CORTEX_RELEASES_URL`, `CORTEX_NO_UPDATE_CHECK`, `CORTEX_NO_REGISTER`, and `CORTEX_HOME` for the home itself. Nothing writes them down, nor says which one is set.
- **The team's folder is `agents/`, fixed** — in the resolver, the catalog, `cortex validate`, the service scan, the runtime and the spec's own text. A team whose overlays serve several workspaces clones them at `agents/` in each.
- **Trying a version is moving it.** At a 2.B root, nothing tells a developer's try from the team's choice.

## 2. Decision

**Cortex reads its settings from five places — the environment, the developer's `cortex.local.toml`, the project's `cortex.toml`, the team's `cortex.team.toml` in the team's folder, and the machine's `config.toml` — and for each key the most specific value wins. Two exceptions: between the developer's version and the team's, the highest wins; a key the team's file locks takes the team's value. What the team commits follows the team's values, never a developer's try. The project names the team's folder, `agents/` by default. `cortex config` says each value and where it comes from. Everything new is optional, read only by a project that resolves 1.8.0 or later, and nothing of 1.7.0 changes meaning: the record ships in 1.8.0, a minor version.**

Decided with the maintainer on 2026-11-06 and 2026-11-07, numbered as the arbitrations of #98 were:

- **12** — an organisation policy, versioned in the team's repository, that gives defaults and locks only the keys it lists;
- **13** — the most specific value wins — local, project, team, machine — and the environment goes over all of them, except on a locked key;
- **14** — the keys distributed per tier, the machine's in `~/.cortex/config.toml`;
- **15** — at a 2.B root, the team pins its version in the team's folder;
- **16** — a `cortex config` that says where each value comes from;
- **17** — the team's folder set per project, a path relative to the workspace root that may lead outside it; a repository named by its git URL waits for #327;
- **18** — the highest version wins between the team's pin and the developer's: a team's pin above the developer's was validated by the team's process, and a developer may try a higher one before proposing it;
- **19** — `version` is never lockable;
- **20** — the lock beside the team's pin, in the team's folder, which follows the configured name.

Out of scope: the IDE modes (#98, its second record); a team's repository named by git URL (#327); the endpoints of local models (#40); the runtime's own variables but `CORTEX_THEME` (§3.12).

## 3. Detailed contract

### 3.1 The tiers, and four terms

| Tier | Where | Written by |
|---|---|---|
| environment | the variables of §3.7 | the developer's shell, a CI |
| local | `cortex.local.toml`, at the project root — never committed | the developer; `cortex sync` (`spec`, `pin_committed`, `claude_entry`, `claude_team_entry`); `cortex upgrade --local` (`version`) |
| project | `cortex.toml`, at the project root | `cortex init`, `cortex upgrade`, the team |
| team | `cortex.team.toml` in the team's folder (§3.6), committed in the team's repository | the team; `cortex upgrade --team` |
| machine | `config.toml` in the Cortex home — `$CORTEX_HOME`, `~/.cortex` by default | the developer |

For each key, the first tier that sets it from the top wins, else its default; `version` and a locked key (§3.5) excepted.

| Term | What it is |
|---|---|
| **the team's version** | a committed `cortex.toml`'s `version` (open arbitration 4); else the team's file's; else an uncommitted `cortex.toml`'s, as 1.7.0 reads it |
| **the developer's version** | `cortex.local.toml`'s `version`, and an uncommitted `cortex.toml`'s when the team's file pins one |
| **the team's resolution** | every tier but `cortex.local.toml`, of which `agents` only; its version is the team's. Every committed file follows it — `cortex.toml`, the team's file, their locks, a committed bootstrap and its personality block — and `--locked` checks it |
| **the full resolution** | every tier; its version is the higher of the developer's and the team's — a pre-release below its release, the team's on a tie. This machine reads it |

- **Committed** means that git tracks the file — ADR-011 §3.5's test — asked of `cortex.toml` for the pin and of the bootstrap for its own; of a linked bootstrap, of the file the link resolves to, in that file's repository. Without `git` on `PATH`, or with a git that refuses, a `.git` at or above the project root counts as committed. `cortex sync` records its answer for the pin in `cortex.local.toml` as `pin_committed`, which the templates and the runtime read: they ask no git.
- **The candidate versions** are the team's file's, `cortex.toml`'s and `cortex.local.toml`'s.
- **The core resolves**, in a new module that reads the files and asks git; the CLI and the runtime read it from there. The grammar stays in `project.py`.

### 3.2 The keys

| Key | Environment | Local | Project | Team | Machine | Default |
|---|---|---|---|---|---|---|
| `version` | — | ✓ | ✓ | ✓ | — | none: required in `cortex.toml` or the team's file |
| `theme` | runtime's `CORTEX_THEME` (§3.12) | ✓ | ✓ | ✓ | — | none: required in `cortex.toml` or the team's file |
| `sync` | — | — | ✓ | ✓ | — | `store` |
| `claude_access` | — | ✓ | ✓ | ✓ | — | unset |
| `agents` | — | ✓ | ✓ | — | — | `agents` |
| `locked` | — | — | — | ✓ | — | none |
| `releases_url` | `CORTEX_RELEASES_URL` | — | — | — | ✓ | GitHub's releases of `xod-labs/cortex` |
| `update_check` | `CORTEX_NO_UPDATE_CHECK` | — | — | — | ✓ | `true` |
| `register` | `CORTEX_NO_REGISTER` | — | — | — | ✓ | `true` |

- **A key a tier does not take is an error in that tier's file**, as an unknown key is (ADR-008 §3.4) — the machine's file aside (§3.7). The keys `cortex sync` writes stay its own; `CORTEX_HOME` stays a variable, since it says where the machine's file is.

### 3.3 Ahead and caught up

- **Ahead**, the developer's version above the team's: `cortex sync`, `upgrade` and `validate` say so every time.

  ```text
  note: Cortex 1.9.0-rc.1 is read — cortex.local.toml's, above the team's 1.8.0 (agents/cortex.team.toml). It is not locked: its release was checked against its SHA256SUMS when downloaded.
  ```

- **Caught up**, at or below the team's: it changes nothing, and they say *remove it* — which `cortex upgrade --local` with the team's version does for `cortex.local.toml`'s. No command edits a developer's key unasked.

### 3.4 The lock

| The resolved version comes from | Its lock |
|---|---|
| a committed `cortex.toml` | `cortex.lock` at the project root — ADR-010 §3.2 |
| the team's file | `cortex.team.lock` beside it, committed in the team's repository, with `cortex.lock`'s grammar |
| an uncommitted `cortex.toml` | `cortex.lock` at the root, the developer's own — as ADR-010 §3.2 has it at a 2.B root |
| `cortex.local.toml` | none, and not checked again: no lock warning, `--strict` passes |

- **`cortex sync` applies ADR-010 §3.3's table to that lock**, and reads no other. The team's lock is written under a lock of the team's folder, which several projects may sync at once; *commit it* names the folder; a lock that cannot be written stops the sync before anything moves.
- **`--locked`** checks the team's resolution: the developer's version and every other key of `cortex.local.toml` but `agents` are dropped. A folder `agents` names, missing or empty — an uninitialised submodule — is an error. The default `agents/`, a submodule left empty, is a warning at sync and validate, which `--strict` fails (ADR-008 §9).

### 3.5 The team's file

```toml
# The team's Cortex settings, for every project that reads this folder (ADR-013). Committed in the team's repository.
version = "1.8.0"
theme = "h2g2"
claude_access = false
locked = ["claude_access"]
```

- **Optional**: without it, there is no team tier. Its keys are `version`, `theme`, `sync`, `claude_access` and `locked`, none required; an unknown key is an error, and so is a `version` below 1.8.0, which would not read the file.
- **`locked`** lists keys the file sets, each once — never `version`, nor `locked`. **`claude_access` is lockable to `false` only**: a team may forbid Claude Code to read the spec without asking, never grant it over a developer who declined it, whose choice ADR-008 §9 lets win.
- **A locked key set otherwise above it** takes the team's value, with a warning at sync and validate; `validate --strict` fails. A flag against a lock — `cortex sync --link`, `cortex init --theme`, `--no-personality`, `--claude-access` — is refused, exit 1, naming the lock. A lock binds what is committed and what CI checks; on a developer's machine it is advisory, since their checkout of the team's folder is theirs to edit.
- **Read in every layout** that has a team's folder: at a 2.B root it is the only committed tier.

### 3.6 The team's folder: `agents`

```toml
agents = "../bluspark-agents"    # cortex.toml — relative to the project root, with /
```

- **Set** in `cortex.toml`, relative with `/` — decision 17 governs this value — or in `cortex.local.toml`, relative or absolute, where this machine has its clone. Never in the team's file, which is in the folder the key names. **Default:** `agents`. A developer whose clone is elsewhere writes `agents` in `cortex.local.toml` before a first `cortex init`, which reads it there.
- **A folder the key names is refused** when it is, in `cortex.toml`, absolute on any platform or written with `\`; anywhere, the project root or an ancestor of it, inside `cortex/` or the store, or a folder holding a project's `cortex.toml`. Folders compare as the file system compares them. **The default `agents/` is never refused**: a project of 1.7.0 reads as before.
- **What follows the folder:** the workspace tier of the cascade (ADR-001 §3.1, ADR-007 §3.1), whose layer directories the folder holds itself; the team's context files (ADR-006 §3.2); the overlay roots `cortex validate` checks; the team's file and lock; the project's own themes; the runtime (§3.12). The service scan still leaves `{root}/agents/` out, and the folder too when it is inside the root. A service's `{service}/agents/` and the `Base:` headers stay.
- **Missing**, a folder the key names is a warning at every sync, and an error under `--locked`. **Left behind**: the root's `agents/` still holding overlays or context files while the key names another folder is warned.
- **Outside the root**, the folder is outside what the AI tool reads by default. With `claude_access`, `cortex sync` writes a second Cortex-owned entry in Claude Code's settings, `claude_team_entry` — only when `agents` comes from `cortex.local.toml`, or the folder holds a `cortex.team.toml`, a layer directory or a team's context file, and never for a hidden directory. It lives as `claude_entry` does: replaced when the folder moves, removed when access is off, the folder inside the root, or the key gone. Other tools may not read the folder; #137 measures them, for 1.9.0.
- **Trust:** a committed file directs reads and writes — the team's lock — outside the project. The refusals keep them off the project's ancestors, the spec and other projects, and Claude Code's entry off a folder that holds nothing of a team's; what is left is a folder the committed `cortex.toml` names, whose change is reviewed as any commit.

### 3.7 The machine's file and the environment

```toml
# This machine's Cortex settings, for every project of this user (ADR-013).
releases_url = "https://mirror.example.com/cortex/releases"
update_check = false
```

- **Optional**, written by no command. An unknown key, a value of the wrong type, or a file that cannot be read is a warning naming it, and the rest is read: `cortex self-update` never fails on it. An invalid `releases_url` is refused as `CORTEX_RELEASES_URL` is today — https, http to this machine only — naming the file.
- **The variables override it**, as they behave in 1.7.0: `CORTEX_RELEASES_URL`, an empty one counting as unset; `CORTEX_NO_UPDATE_CHECK` and `CORTEX_NO_REGISTER`, set and not empty, make `update_check` and `register` false — no variable turns them back on. `cortex self-update`'s note on its host names the variable or the file.
- **The binary's, not a version's**: read whatever version a project resolves, and by `cortex self-update` outside a project. The install scripts read their own variables, never this file.

### 3.8 Which version reads what

What this record adds — the team's file, a local `version`, `agents`, a `cortex.toml` without `version` or `theme` — is read only by a project that resolves **1.8.0 or later**: an earlier version's templates know none of it.

1. **Every file's `version`, and `agents`, are read loosely first**, with §3.6's refusals of the text, and the version resolved.
2. **A version newer than the binary** stops the commands that use the spec — `sync`, `init`, `validate`, `upgrade` without `VERSION` — before any other key is checked. The refusal names the file that holds it and the remedy: `cortex self-update`; `cortex upgrade --local` to drop a try; `--team` only for the team's pin. `cortex upgrade [--team] VERSION` may still move down (ADR-010 §3.5), and refuses before writing when the version resolved after the move is still too new. `cortex config` prints the version and says the binary is too old for it; `prune` and `project list` read loosely.
3. **Then the keys are checked.** In a project that resolves an earlier version, a local `version`, `agents` or a version-less `cortex.toml` is refused, naming 1.8.0; a team's file is a warning — *not read: this project resolves 1.7.0*. Moving a project below 1.8.0 refuses one that uses them, naming them.
4. **`cortex init`** writes `version` and `theme` when the resolved version's templates predate 1.8.0, and otherwise not those the team's file sets: a `version` written from the binary would put a developer ahead of the team on their first init.

### 3.9 `cortex config`

```text
cortex config [KEY] [--locked] [--json]
```

```text
version        1.9.0-rc.1    cortex.local.toml — above the team's 1.8.0 (agents/cortex.team.toml)
theme          h2g2          agents/cortex.team.toml
claude_access  false         agents/cortex.team.toml — locked
agents         agents        default
releases_url   https://github.com/xod-labs/cortex/releases    default
register       false         CORTEX_NO_REGISTER
```

Each key, its value and its origin — a file as the project root names it, a variable, or *default*. `KEY` prints the value alone, an unknown key exiting 2; outside a project, only the machine's keys are known. `--locked` gives the team's resolution; `--json` an object with a `format` field and, per key, the value, the origin, the values it overrode and whether it is locked. It writes nothing; a refused file exits 1, naming it.

### 3.10 The bootstrap: format 3

The templates ask the assistant to read a file it did not read, which raises the format (ADR-010 §3.1) to **3**; ADR-012's end marker and two parts carry over. Their first rules become:

- **`cortex sync` refuses `cortex.toml` for a key, or a missing `version` or `theme`, or leaves a spec of another version** → run `cortex --version`; below 1.8.0, `cortex self-update`, then `cortex sync`. Nothing runs while the machine is in step.
- **The team's folder** is `agents` in `cortex.local.toml`, else in `cortex.toml`, else `agents/`; the team's file is `cortex.team.toml` there. The cascade's steps name the team's folder where they said `agents/`. The block names no folder, so a machine's own `agents` changes no committed file.
- **The version:** a spec of another version than the highest `version` of `cortex.local.toml`, `cortex.toml` and — unless `cortex.local.toml` says `pin_committed = true` and `cortex.toml` pins one — the team's file → run `cortex sync`.
- **The theme** is `theme` in `cortex.local.toml`, else `cortex.toml`, else the team's file — the team's file's own when it locks `theme`.
- **The assistant never moves a pin of its own accord** (ADR-010 §3.8) — the team's file's and a local `version` as `cortex.toml`'s.

Which template writes the block:

- **A committed bootstrap** follows the team's resolution, during a try too: a format a developer tries is not tried there. `cortex upgrade` writes an untouched one again when its text is not that version's template (ADR-010 §3.5, step 1); otherwise `cortex sync` and `validate` say *the bootstrap is not the template of 1.9.0, which the project reads — `cortex upgrade` writes it again*, comparing with the team's version's templates, fetched as sync fetches a version.
- **An untracked bootstrap** is the developer's own and follows the full resolution: in a project that has a team's file and resolves 1.8.0 or later, `cortex sync` and `--local` write an untouched one again.
- **A team's version below 1.8.0** gets no format 3 block.

### 3.11 The commands

- **`cortex sync`** puts the resolved version in the store, checks its lock (§3.4), records `pin_committed`, writes an untracked bootstrap (§3.10), and gives the notes.
- **`cortex upgrade`** moves `cortex.toml`'s `version`, as in 1.7.0. On a `cortex.toml` without one while the team's file pins, a move is refused, exit 1, naming `--team` and `--local`; without `VERSION`, or with the version resolved, it resumes — sync, lock, untouched blocks — as ADR-010 §3.5 does on a pinned version. The overlays and changes are checked against the moved file's target — the team's resolution after the move, or the full one for `--local` — and the full resolution reported when it differs. `--all` (ADR-011 §3.5) moves the projects that pin their own version, and reports one that follows the team's file: *follows the team's 1.8.0*.
- **`--team`** writes the team's file, its lock, and the current project's untouched committed block, which it names as left uncommitted. `--commit` acts in the repository the team's folder is in. **`--all --team`** moves each distinct team's file once, checks the overlays of every registered project that follows it before writing, then runs ADR-010 §3.5's step 5 in each.
- **`--local`** writes `cortex.local.toml`'s `version`, syncs, and writes an untracked bootstrap only; at or below the team's version it removes that key, and says so. It refuses `--branch`, `--commit`, `--all` and `--team`, exit 2. No upgrade writes a committed file from a developer's try.
- **`cortex validate`** compares the spec with the resolved version, checks the lock, and warns on a locked key overridden, a missing or left-behind folder, and a committed bootstrap behind.
- **`cortex prune`** keeps, of every project — registered or under a `DIR` — its candidate versions and the version of the spec read now; its loose read covers the team's file.
- **`cortex project list`** gives as `PINNED` the team's version; `--json` adds its origin.

### 3.12 The runtime

- **It asks no git**: its version is the team's — by `pin_committed` when `cortex sync` recorded it; otherwise `cortex.toml`'s when it pins one, else the team's file's. Of `cortex.local.toml` it reads `agents`, `version` and `pin_committed`, never `spec`, a host's path (ADR-008 §3.6): a `422`, naming the key, only when the full resolution's version or folder differs from the runtime's.
- **Its `CORTEX_THEME`** is the environment tier of `theme`, under a lock: the only key where the environment meets a lock in 1.8.0 (decision 13).
- **A team's folder outside the project root** is a `422` naming it, the path compared as written: the container mounts the project and the store, nothing beside them. The default `agents/` is never refused.

### 3.13 Continuous integration

A project with no team's file keeps the README's recipe of 1.7.0. One with a team's file installs the latest release, reads `cortex config version --locked` — which answers whatever the version — installs the binary of that version, then runs `cortex sync --locked` and `cortex validate --strict`; a team's folder that is its own repository is checked out where the project names it. **A team's repository checked alone** uses a scratch project beside its checkout, whose `cortex.toml` holds `agents = "../the-team-repository"`. The README gives both.

### 3.14 What other records record

Once this record is implemented, each of these records it in its amendments: ADR-001 §3.1, ADR-006 §3.2 and ADR-007 §3.1 (the team's folder, a root of the cascade); ADR-008 §3.4 (the keys), §3.6 (the runtime's resolution and its `422`s) and §9 (the 2.B pin, `claude_access` lockable to `false` only); ADR-010 §3.1 (format 3), §3.2 to §3.5 (the resolved version, the lock's place, `cortex sync` writing an untracked bootstrap), §3.6 (prune) and §3.8 (the rules); ADR-011 §3.2 (`PINNED`), §3.4 (prune) and §3.5 (`--all`).

## 4. Phases

Execution order **1 → 2 → 3**, in one release, 1.8.0, which ships with #331 (#328, #329, `cortex upgrade`'s ending outside git, the workspace template's step 4.2).

### Phase 1 — The resolution, the machine's file and `cortex config`

§3.1, §3.2, §3.7, §3.9 and steps 1–2 of §3.8, for the keys of 1.7.0 and the machine's.

- the core's golden outputs are unchanged; a project of 1.7.0 resolves to what 1.7.0 reads, and a 1.8 sync leaves its files byte for byte
- at a 2.B root without a team's file, `sync --locked`, `config --locked` and the runtime read `cortex.toml`'s version; the runtime image, on a host-owned read-only mount, resolves what the host's `cortex config --locked` prints
- a git that refuses counts as no git; a project in a subdirectory of its repository, its `cortex.toml` tracked, records `pin_committed = true`
- the machine's file: an unknown key or a wrong type warns, and `cortex self-update` still runs; an invalid `releases_url` is refused naming the file; an empty `CORTEX_RELEASES_URL` is unset
- a version newer than the binary stops `sync`, `init`, `validate` and `upgrade` without `VERSION`, naming the file and the remedy, whatever key a file holds; `cortex upgrade VERSION` still moves down, and refuses when the result is still too new; `config` and `config --locked` print that version and say so; `cortex config KEY` with an unknown key exits 2, and outside a project knows the machine's keys only

### Phase 2 — The team's file, the version, the lock and format 3

§3.3 to §3.5, §3.8 steps 3–4, §3.10 to §3.13, but for the team's folder.

- a local `1.9.0-rc.1` over a team's `1.8.0` is read, with the note; at `1.8.0` it is said to change nothing; a committed `cortex.toml` at `1.7.0` beside a team's file at `1.8.0` reads `1.7.0`, with the team's file warned of as not read
- a local version writes no lock, warns of none, passes `--strict`; `--locked` drops it; the team's lock is written under a lock of its folder; a missing folder the key names fails `--locked`, the default one empty warns
- a team's file whose `version` is below 1.8.0 is refused; `locked` with `version`, `locked`, a key it does not set or twice, or `claude_access = true` is refused; a locked key overridden warns at sync and fails `validate --strict`; `cortex sync --link` against a locked `sync` exits 1
- `--team` and `--all --team` move a team's file once and step-5 its followers only; `--all --local` and `--team --local` exit 2; with a local try, `cortex upgrade` and `--all --commit` write no bootstrap; `--local` at the team's version removes the local key; a plain upgrade on a version-less `cortex.toml` resumes without `VERSION` and refuses a move
- both templates are format 3 with §3.10's rules, the init matrix differing by those lines only; 1.7.0's `cortex upgrade` on an untouched format 3 file writes its own format 2 block and puts the team's part back; a 1.8 sync writes format 3 back over an untracked file only
- the runtime reads the team's version, `CORTEX_THEME` under a lock, and a local `version` above it is a `422`
- the README's CI recipe passes on a project whose version is the team's file's, and on one of 1.7.0; the docs' adoption path — the team's move announced in `agents/project-context.md` — brings a teammate on 1.7.0 to the team's version
- in real Claude Code conversations on a 2.B workspace: with a 1.7.0 binary, the assistant runs `cortex self-update`, then `cortex sync`; with a team's file pinning a version the spec is not of, it runs `cortex sync`, then reaches the Prompt Manager's card with the theme the team's file locks; in step, it runs nothing. The other tools are #137's, for 1.9.0

### Phase 3 — The team's folder

§3.6, and §3.8 for `agents`. The core's cascade takes the team's folder as a layer directory itself, where it took a tier's root and added `agents/`: `resolver`, `catalog`, `validate` and `workspace` change their signature. `agents/roles/prompt-manager.md` and `agents/workflows/README.md` name *the team's folder the bootstrap names*; the runtime's `context.py` and `run.py` read it.

- `agents = "../x"` moves the workspace tier, the team's context files, the overlay roots, the team's file and lock, and the themes; the core's golden outputs are unchanged without it
- in `cortex.toml`, `/x`, `C:\x`, `\\server\x` and `..\x` are refused, and so are the root, an ancestor, a folder inside `cortex/` or the store, and one holding a project's `cortex.toml`; the default `agents/` never is; a missing folder, and a root `agents/` left behind, warn at sync
- with `claude_access`, a folder outside the root that holds a layer directory gets `claude_team_entry`, `agents = "../../.ssh"` gets none, and the entry goes when the key does; a linked bootstrap is asked of git in its own repository; in a real Claude Code conversation with `agents = "../x"`, the assistant reads a role overlay from it
- the runtime returns a `422` for a folder outside the root, and for a local `agents` that names another folder

## 5. Consequences

### Positive
- A team pins its version at a 2.B root: one commit in its repository moves every teammate whose binary and bootstrap are of 1.8.0 or later.
- A developer tries a version before the team, without a branch and without touching a committed file, and is told at every sync.
- The machine's settings have a file, `cortex config` says why a command reads what it reads, and a team's folder can serve several workspaces, cloned once beside them.

### Negative
- **The first move reaches no teammate alone.** A format 2 bootstrap does not read the team's file: each teammate runs `cortex self-update` once, when their binary is before 1.8.0, and `cortex sync` once, by hand. The docs suggest announcing it in `agents/project-context.md`, which format 2's workspace step 2 reads.
- **The assistant reads one more file**, the team's — the kind of reading ADR-012 §6 declined for a team's rules, which #137 has not measured outside Claude Code. The IDE modes remove it.
- **A cortex before 1.8.0** ignores the team's file, and refuses a `cortex.toml` using a key of 1.8.0, or lacking `version` or `theme`, as such rather than as a binary too old. 1.7.0's `cortex upgrade` writes an untouched format 3 file again from its own format 2 template, the team's part kept; a 1.8 sync puts format 3 back only where the file is untracked. Runtime images before 1.8.0 refuse such a `cortex.toml` with a `422`, and ignore the team's file and `cortex.local.toml`.
- **A team's folder outside the root** is read by Claude Code through its entry, not yet by the other tools, nor by the runtime.

### Neutral
- `cortex.toml` and `cortex.local.toml` keep their names, place and meaning; the variables keep their names and effect.

## 6. Alternatives considered

- **The developer's pin over the team's** — ADR-008 §9's wording. A developer who has not moved would hold the version the team validated back. Rejected by the maintainer (18).
- **A lockable `version`**, which would forbid trying a version before proposing it. Rejected by the maintainer (19).
- **The lock at a 2.B root**, committed nowhere — rejected by the maintainer (20) — or inside the team's file, mixing what the team writes and what `cortex sync` writes, which ADR-010 §3.2 keeps apart. Rejected.
- **The configured folder written into Cortex's block**: the block is committed, and `agents` may be one machine's. Rejected: the templates read the key (§3.10).
- **The templates guessing committedness from a `.git`**: wrong for a project in a subdirectory of its repository, or an untracked pin. Rejected: `cortex sync` records it.
- **A new CLI variable per key**, `CORTEX_VERSION` and the like, which would change a committed setting with a shell — the runtime's existing `CORTEX_THEME` stays — and **`releases_url` in a committed tier**, which would let a commit redirect every teammate's downloads. Rejected.

## 7. Follow-ups (out of scope for this ADR)

1. **The IDE modes** (#98, its second record) — the resolved values rendered into Cortex's block, which removes §3.10's comparisons.
2. **A team's repository shared by git URL** (#327).
3. **A team's folder outside the root** read by the runtime — a mount — and by the other tools (#137).
4. **The endpoints of local models** (#40), in the machine's file.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.3 the store, §3.4 the project's files, §3.6 the runtime, §7.1 this follow-up, §9 the 2.B pin, `claude_access` and a later binary's checks
- [ADR-010](ADR-010-lock-and-upgrade.md) — §3.1 the format, §3.2 to §3.6 the lock, `sync`, `validate`, `upgrade`, `prune`, §3.8 the bootstrap's rules
- [ADR-011](ADR-011-project-registry.md) — §3.2, §3.4, §3.5 and its committed test
- [ADR-007](ADR-007-cortex-core.md) §3.1, [ADR-006](ADR-006-workspace-shareable-repo.md) §3.2, [ADR-001](ADR-001-layered-overrides.md) §3.1 — the cascade's roots; [ADR-012](ADR-012-bootstrap-block.md) — Cortex's block, §5 an older cortex and a later format
- Issues: [#98](https://github.com/xod-labs/cortex/issues/98), [#327](https://github.com/xod-labs/cortex/issues/327), [#137](https://github.com/xod-labs/cortex/issues/137), [#40](https://github.com/xod-labs/cortex/issues/40); [#331](https://github.com/xod-labs/cortex/pull/331)

## 9. Amendments

None yet.

## Open arbitrations

For the maintainer, before acceptance; each answer is folded into the text, and this section removed.

1. **The team's file's name.** (b) `cortex.team.toml` beside `cortex.team.lock`, as written; (a) `cortex.toml` and `cortex.lock`, as arbitration 15 named it. Under (a), a command run in a team's folder outside the root takes the file for a project's, `agents = "../other-project"` adopts another project's pin, a 1.7 workspace holding `agents/cortex.toml` changes meaning, and a cortex before 1.8.0 may treat `agents/` as a project. **Recommended: (b)** — it departs from the name arbitration 15 gave, not from its decision.
2. **What `cortex upgrade` moves by default once a team's file exists.** (a) `cortex.toml`, as in 1.7.0 — on a version-less one, a move refused with `--team` and `--local` named, a resume carried out (§3.11); (b) the team's file whenever it pins. **Recommended: (a)** — a default that does not change with the project's files, nor within the minor version.
3. **The bootstrap and the team's file.** (a) The templates find the folder through `cortex.local.toml`, then `cortex.toml`, read the team's file there with the `pin_committed` sync records, and ask a binary below 1.8.0 to update (§3.10); (b) `cortex sync` writes the resolved values in `cortex.local.toml`, which alone the templates read — a team's move then seen at the next sync, not the next conversation. **Recommended: (a)** — the rule exists to catch a teammate's move (ADR-010 §3.8).
4. **In a committed project, `cortex.toml` against the team's file.** (b) The committed `cortex.toml` wins, the most specific (decision 13), as written: a project sharing the folder may pin its own version, behind or ahead, and keeps one lock. (a) The highest wins between them too: a higher pin was validated by the team's process (decision 18's reasoning), but a project sharing the folder cannot stay behind, and two locks may exist, the losing one stale. **Recommended: (b).**
