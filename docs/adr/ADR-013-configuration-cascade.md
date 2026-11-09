# ADR-013 — The configuration cascade: the team's settings, the machine's, and where each value comes from

- **Status:** Accepted
- **Date:** 2026-11-07 (proposed and accepted, with the maintainer's arbitrations of 2026-11-06 and 2026-11-07, §2)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `core/cortex_core/` (a new module for the resolution; `project.py`, the grammar; `resolver.py`, `catalog.py`, `validate.py`, `workspace.py`, the team's folder), `cli/cortex_cli/` (`config`, `sync`, `upgrade`, `init`, `validate`, `registry`, `prune`, `store`, `self_update`, `main`, and a new `cortex config`), `runtime/cortex_runtime/` (`base.py`, `context.py`, `run.py`, `__main__.py`), `templates/bootstrap-instructions*.md` (format 3), `agents/roles/prompt-manager.md` and `agents/workflows/README.md` (the team's folder), `README.md` (its CI recipe and its variables), `docs/getting-started.md`, `docs/extending-layers.md`, `docs/creating-a-theme.md`
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) §3.3 (`config.toml`, reserved), §3.4, §3.6, §7.1 (this follow-up) and §9 · [ADR-010](ADR-010-lock-and-upgrade.md) §3.1 to §3.6 and §3.8 · [ADR-011](ADR-011-project-registry.md) §3.2, §3.4 and §3.5 · [ADR-007](ADR-007-cortex-core.md) §3.1 · [ADR-006](ADR-006-workspace-shareable-repo.md) §3.2 · [ADR-001](ADR-001-layered-overrides.md) §3.1 · [ADR-012](ADR-012-bootstrap-block.md) · epic [#98](https://github.com/xod-labs/cortex/issues/98), first of its two records · [#327](https://github.com/xod-labs/cortex/issues/327) · [#137](https://github.com/xod-labs/cortex/issues/137) · [#40](https://github.com/xod-labs/cortex/issues/40)

---

## 1. Context

ADR-008 left the configuration cascade to #98 (§7.1): organisation policy ▸ project ▸ project-local ▸ machine, *of which `cortex.toml` and `cortex.local.toml` are the first two files*, and `~/.cortex/config.toml` reserved for the machine (§3.3). On 2026-11-06 the maintainer cut #98 in two: this record, then the IDE modes, which #137 will validate. Four gaps have shown since:

- **A team that does not commit its root cannot pin its version.** At a workspace root that is no git repository — flavor 2.B of ADR-006 — `cortex.toml` is committed nowhere, and pinning is per developer (ADR-008 §9). Moving Bluspark's workspace to 1.7.0 on 2026-11-07 moved one machine. ADR-008 §9 left the answer here: a pin in the team's `agents/` repository.
- **The machine's settings are variables only** — `CORTEX_RELEASES_URL`, `CORTEX_NO_UPDATE_CHECK`, `CORTEX_NO_REGISTER`, and `CORTEX_HOME` for the home — and nothing says which one is set.
- **The team's folder is `agents/`, fixed** — in the resolver, the catalog, `cortex validate`, the service scan, the runtime and the spec's own text: a team cannot name it otherwise.
- **Trying a version is moving it.** At a 2.B root, nothing tells a developer's try from the team's choice.

## 2. Decision

**Cortex reads its settings from five places — the environment, the developer's `cortex.local.toml`, the project's `cortex.toml`, the team's `cortex.team.toml` in the team's folder, and the machine's `config.toml` — and for each key the most specific value wins. Two exceptions: between the developer's version and the team's, the highest wins; a key the team's file locks takes the team's value. What the team commits follows the team's values, never a developer's try. The project may rename the team's folder, `agents/` by default, inside the workspace. `cortex config` says each value and where it comes from. Everything new is optional — a project's new keys and files read only once the team's version is 1.8.0 or later, the machine's file whatever the version — and nothing of 1.7.0 changes meaning: the record ships in 1.8.0, a minor version.**

Decided with the maintainer, numbered as the arbitrations of #98 were. On 2026-11-06 and 2026-11-07:

- **12** — an organisation policy, versioned in the team's repository, that gives defaults and locks only the keys it lists;
- **13** — the most specific value wins — local, project, team, machine — and the environment goes over all of them, except on a locked key;
- **14** — the keys distributed per tier, the machine's in `~/.cortex/config.toml`;
- **15** — at a 2.B root, the team pins its version in the team's folder;
- **16** — a `cortex config` that says where each value comes from;
- **17** — the team's folder set per project, a path relative to the workspace root that may lead outside it; a repository named by its git URL waits for #327 — narrowed by 30;
- **18** — the highest version wins between the team's pin and the developer's: a team's pin above the developer's was validated by the team's process, and a developer may try a higher one before proposing it;
- **19** — `version` is never lockable;
- **20** — the lock beside the team's pin, in the team's folder, which follows the configured name.

On 2026-11-07, answering this record's draft:

- **21** — the team's file and its lock are `cortex.team.toml` and `cortex.team.lock`;
- **22** — `cortex upgrade` moves `cortex.toml` by default; `--team` and `--local` move the others — narrowed on 2026-11-07, after the counter-review: in a project that follows a team's file, an uncommitted `cortex.toml` included, a plain move is refused (§3.11);
- **23** — the templates compare each file's `version`, and the spec's, with `synced` by plain equality;
- **24** — in a committed project, a committed `cortex.toml`'s version wins over the team's file;
- **25** — `claude_access` is lockable to `false` only: a team may forbid a permission, never grant one on a developer's machine;
- **26** — `releases_url` is the machine's only;
- **27** — the key that names the team's folder is `agents`;
- **28** — `cortex upgrade --local` at or below the team's version removes the local key, with no new option;
- **29** — `cortex sync` records `synced` in `cortex.local.toml`;
- **30** — the same configuration everywhere: `agents` is set in a committed `cortex.toml` only, and names a folder inside the workspace root — it renames the team's folder, nothing more. A shared repository elsewhere stays #327's.

Out of scope: the IDE modes (#98, its second record); a team's repository outside the workspace (#327); the endpoints of local models (#40); the runtime's own variables but `CORTEX_THEME` (§3.12).

## 3. Detailed contract

### 3.1 The tiers, and four terms

| Tier | Where | Written by |
|---|---|---|
| environment | the variables of §3.7 | the developer's shell, a CI |
| local | `cortex.local.toml`, at the project root — never committed | the developer; `cortex sync` (`spec`, `synced`, `claude_entry`); `cortex upgrade --local` (`version`) |
| project | `cortex.toml`, at the project root | `cortex init`, `cortex upgrade`, the team |
| team | `cortex.team.toml` in the team's folder (§3.6), committed in the team's repository | the team; `cortex upgrade --team` |
| machine | `config.toml` in the Cortex home — `$CORTEX_HOME`, `~/.cortex` by default | the developer |

For each key, the first tier that sets it from the top wins, else its default; `version` and a locked key (§3.5) excepted.

| Term | What it is |
|---|---|
| **the team's version** | a committed `cortex.toml`'s `version` (decision 24); else the team's file's; else an uncommitted `cortex.toml`'s, as 1.7.0 reads it |
| **the developer's version** | `cortex.local.toml`'s `version`, and an uncommitted `cortex.toml`'s when the team's file pins one |
| **the team's resolution** | every tier but `cortex.local.toml`, an uncommitted `cortex.toml`'s keys only where the team's file sets none; its version is the team's. A project **follows** the team's file when its `cortex.toml` holds no committed `version`. Every committed file follows the team's resolution — `cortex.toml`, the team's file, their locks, the team's bootstrap and its personality block |
| **the full resolution** | every tier; its version is the higher of the developer's and the team's — a pre-release below its release, the team's on a tie. This machine reads it |

- **Committed**, of `cortex.toml`, means that git tracks it — ADR-011 §3.5's test, asked of the root's own repository: git's variables of the caller, a hook's `GIT_DIR`, are not followed; without `git` on `PATH`, or with a git that refuses, a `.git` at or above the project root counts. **The team's bootstrap** is any but the developer's own — one git ignores, or one in no repository; a linked bootstrap is asked of the file the link resolves to, in that file's repository.
- **`synced`** (decision 29), written by `cortex sync` in `cortex.local.toml`, records the `version` read from each file — the **candidate versions**, `team`, `project` and `local` — the team's version, `pinned`, and the version synced, `reads`: `synced = { team = "1.8.0", project = "1.7.0", pinned = "1.8.0", reads = "1.8.0" }`. `team`, `project` and `local` are versions, each left out for a file without one; `pinned` and `reads` are required, `reads` a version, or `""` after `sync --from`, and the version checked after `--locked`; an unknown sub-key is refused. The core's grammar, which takes strings and booleans today, takes this table. A `synced` that cannot be read is rewritten by the sync: a cache of `cortex sync`'s never blocks it. Written only where the team's version is 1.8.0 or later, and removed otherwise, a move below 1.8.0 included — a cortex before 1.8.0 refuses a `cortex.local.toml` that holds it.
- **The core resolves**, in a new module that reads the files and takes the committed-or-not answer from its caller: the CLI asks git, the runtime reads `synced`. The grammar stays in `project.py`.

### 3.2 The keys

| Key | Environment | Local | Project | Team | Machine | Default |
|---|---|---|---|---|---|---|
| `version` | — | ✓ | ✓ | ✓ | — | none: required in `cortex.toml` or the team's file |
| `theme` | runtime's `CORTEX_THEME` (§3.12) | ✓ | ✓ | ✓ | — | none: required in `cortex.toml` or the team's file |
| `sync` | — | — | ✓ | ✓ | — | `store` |
| `claude_access` | — | ✓ | ✓ | ✓ | — | unset |
| `agents` | — | — | ✓ | — | — | `agents` |
| `locked` | — | — | — | ✓ | — | none |
| `releases_url` | `CORTEX_RELEASES_URL` | — | — | — | ✓ | GitHub's releases of `xod-labs/cortex` |
| `update_check` | `CORTEX_NO_UPDATE_CHECK` | — | — | — | ✓ | `true` |
| `register` | `CORTEX_NO_REGISTER` | — | — | — | ✓ | `true` |

- **A key a tier does not take is an error in that tier's file**, as an unknown key is (ADR-008 §3.4) — the machine's file aside (§3.7). The keys `cortex sync` writes stay its own; `CORTEX_HOME` stays a variable, since it says where the machine's file is. The CLI and the templates have no environment tier for `theme`: `CORTEX_THEME` is the runtime's. `releases_url` is the machine's only (decision 26): a commit to a team's repository never sends every teammate's downloads to another host.

### 3.3 Ahead and caught up

- **Ahead**, the developer's version above the team's: `cortex sync`, `upgrade` and `validate` say so every time — *Cortex 1.9.0-rc.1 is read — cortex.local.toml's, above the team's 1.8.0 (agents/cortex.team.toml)* — and, of `cortex.local.toml`'s, that it is not locked, its release checked against its `SHA256SUMS` when downloaded.
- **Caught up**, at or below the team's: it changes nothing, and they say *remove it* — which `cortex upgrade --local` with the team's version does for `cortex.local.toml`'s (decision 28). No command edits a developer's key unasked.

### 3.4 The lock

| The resolved version comes from | Its lock |
|---|---|
| a committed `cortex.toml` | `cortex.lock` at the project root — ADR-010 §3.2 |
| the team's file | `cortex.team.lock` beside it, committed in the team's repository, with `cortex.lock`'s grammar |
| an uncommitted `cortex.toml` | `cortex.lock` at the root, the developer's own — as ADR-010 §3.2 has it at a 2.B root |
| `cortex.local.toml` | none, and not checked again: no lock warning, `--strict` passes |

- **`cortex sync` applies ADR-010 §3.3's table to that lock**, and reads no other: during a try it leaves the committed lock alone. **`cortex upgrade`** writes the lock of the version it moves — `cortex.lock` for `cortex.toml`'s, the team's for `--team` — from that version in the store, whatever this machine reads, and its resume checks that lock — during a try, the lock of the team's version, which the sync leaves, is written by the resume, which `--locked` names with the version: `cortex upgrade 1.8.0`. The team's lock is written under a lock of the team's folder, which several projects may sync at once; *commit it* names the folder. Missing or stale in a folder this user cannot write, it is a warning on a developer's machine, an error under `--locked`.
- **`--locked`** drops the developer's version — `cortex.local.toml`'s, and an uncommitted `cortex.toml`'s while the team's file pins — for the version and the lock it checks; `spec` then names the version checked, and Claude Code's entry follows the other keys as in 1.7.0. A folder `agents` names, missing or empty — an uninitialised submodule — is an error. The default `agents/`, a submodule left empty — by the `.gitmodules` beside it, or by git's index, the project in a subdirectory of its repository — is a warning at sync and validate, which `--strict` fails (ADR-008 §9). A version-less `cortex.toml` whose team's folder is missing, or an empty submodule, is refused, naming the folder and `git submodule update --init`. A committed `cortex.toml` that drops its `version` leaves a `cortex.lock` nothing reads: `cortex sync` says so.

### 3.5 The team's file

```toml
# The team's Cortex settings, for every project that reads this folder (ADR-013). Committed in the team's repository.
version = "1.8.0"
theme = "h2g2"
claude_access = false
locked = ["claude_access"]
```

- **Optional**: without it, there is no team tier. Its keys are `version`, `theme`, `sync`, `claude_access` and `locked`, none required; an unknown key is an error, and so is a `version` below 1.8.0, which would not read the file. It is read in every layout that has a team's folder: at a 2.B root it is the only committed tier.
- **`locked`** lists keys the file sets, each once — never `version`, nor `locked`.
- **`claude_access` is lockable to `false` only** (decision 25). A team may forbid a permission, never grant one on a developer's machine: locked to `false`, Claude Code asks before it reads the spec, whatever a developer sets; locked to `true`, it would read the spec without asking over a developer who declined it, whose choice ADR-008 §9 lets win — so `claude_access = true` in `locked` is refused.
- **A locked key set otherwise above it** takes the team's value, with a warning at sync and validate; `validate --strict` fails. A flag against a lock — `cortex sync --link`, `cortex init --theme`, `--no-personality`, `--claude-access` — is refused, exit 1, naming the lock. A lock binds what is committed and what CI checks; on a developer's machine it is advisory, since their checkout of the team's folder is theirs to edit.

### 3.6 The team's folder: `agents`

```toml
agents = "team-agents"    # cortex.toml — the team's folder, renamed
```

- **Set in a committed `cortex.toml` only** (decision 30) — never in an uncommitted one, as at a 2.B root, nor in `cortex.local.toml`, the team's file or the machine's: every developer reads the same folder. Its value is a relative path, with `/`, naming a folder inside the workspace root. **Default:** `agents`.
- **Refused:** `agents` in an uncommitted `cortex.toml` — the team's folder is then `agents/`; a `..` component, a `\`, an absolute path of any platform — `/x`, `C:\x`, `\\server\x`; the project root itself; `cortex/` or a folder inside it; a service's `{service}/agents/` or a folder inside a service; a folder holding a project's `cortex.toml`; a component starting with `.`. Paths compare as written, with the file system's rules for names — in any case on macOS and Windows.
- **A symbolic link:** a team's folder, the default `agents/` included, may be a link leading outside the workspace. Cortex checks its path as written, inside the workspace, and resolves no link: the link is the developer's own choice, as in 1.7.0 — #327's simple form — and Cortex names no path outside the workspace. **The default `agents/` is never refused**: a project of 1.7.0 reads as before.
- **What follows the folder:** the workspace tier of the cascade (ADR-001 §3.1, ADR-007 §3.1), whose layer directories the folder holds itself; the team's context files (ADR-006 §3.2); the overlay roots `cortex validate` checks; the team's file and lock; the project's own themes; the runtime. The service scan leaves out `{root}/agents/` and the renamed folder. A service's `{service}/agents/` and the `Base:` headers stay. **Missing**, a folder the key names is a warning at every sync, and an error under `--locked`. **Left behind**: the root's `agents/` still holding overlays or context files while the key names another folder is warned.

### 3.7 The machine's file and the environment

- **`config.toml` in the Cortex home** takes the machine's keys of §3.2; optional, written by no command. An unknown key, a value of the wrong type, or a file that cannot be read is a warning naming it, and the rest is read: `cortex self-update` never fails on it. An invalid `releases_url` is refused as `CORTEX_RELEASES_URL` is today — https, http to this machine only — naming the file; `cortex config` prints it, with a warning that a download refuses it.
- **The variables override it**, as they behave in 1.7.0: `CORTEX_RELEASES_URL`, an empty one counting as unset; `CORTEX_NO_UPDATE_CHECK` and `CORTEX_NO_REGISTER`, set and not empty, make `update_check` and `register` false — no variable turns them back on. `cortex self-update`'s note on its host names the variable or the file.
- **The binary's, not a version's**: read whatever version a project resolves, and by `cortex self-update` outside a project. The install scripts read their own variables, never this file.

### 3.8 Which version reads what

What this record adds — the team's file, a local `version`, `agents`, a `cortex.toml` without `version` or `theme` — is read only once the team's version is **1.8.0 or later**: an earlier version's templates know none of it.

1. **Every file's `version`, and a committed `cortex.toml`'s `agents`, are read loosely first**, with §3.6's refusals of the text, and the version resolved.
2. **A resolved version newer than the binary** stops the commands that use the spec — `sync`, `init`, `validate`, `upgrade` without `VERSION` — before any other key is checked. The refusal names the file that holds it and the remedy: `cortex self-update`; `cortex upgrade --local VERSION` for a try; `cortex upgrade --team VERSION` for the team's pin. `cortex upgrade [--team | --local] VERSION` may still move down (ADR-010 §3.5), and refuses before writing when the version resolved after the move is still too new. `cortex config` prints the version and says the binary is too old for it; `prune` and `project list` read loosely. `--from`, which checks no version as in 1.7.0, and `validate` of a spec that names none, pass the gate; a spec that is not there names none, and is no checkout's: the version is said first. **A source checkout or a development build** — `0.0.0-dev`, `0.0.0-dev.N` — passes the gate: refused, it would refuse what 1.7.0 accepted, a project pinned to a release validated from source among them. The store still serves it only what it can, and `cortex upgrade` with no `VERSION` still never moves a project down to it. `CORTEX_SOURCE_VERSION` still names a release where a test needs one.
3. **Then the keys are checked, against the team's version.** While it is before 1.8.0, a local `version` is refused, naming the team's move to 1.8.0; `agents` and a version-less `cortex.toml` are refused, naming 1.8.0; a team's file is a warning — *not read: the team's version is 1.7.0* — and so is a key of the team's file that a version after the team's version adds. A move below 1.8.0 refuses a project that uses them or has no `theme` in `cortex.toml`, naming them; `--team VERSION` below 1.8.0 is refused before anything is written.
4. **`cortex init`** writes `version` and `theme` when the team's version predates 1.8.0, and otherwise not those the team's file sets — a `version` from the binary would put a developer ahead of the team — and writes each bootstrap from the resolution it follows (§3.10).

### 3.9 `cortex config`

```text
cortex config [KEY] [--locked] [--json]
```

```text
version        1.9.0-rc.1    cortex.local.toml — above the team's 1.8.0 (agents/cortex.team.toml)
theme          h2g2          agents/cortex.team.toml
claude_access  false         agents/cortex.team.toml — locked
register       false         CORTEX_NO_REGISTER
```

Each key, its value and its origin — a file as the project root names it, a variable, or *default*. `KEY` prints the value alone, an unknown key exiting 2; outside a project, only the machine's keys are known. `--locked` gives the team's resolution; `--json` an object with a `format` field and, per key, the value, the origin, the values it overrode and whether it is locked. It writes nothing; a refused file exits 1, naming it. A `releases_url` a download would refuse is printed, with a warning, and exits 0.

### 3.10 The bootstrap: format 3

The templates ask the assistant to read a file it did not read, which raises the format (ADR-010 §3.1) to **3**; ADR-012's end marker and two parts carry over. Their first rules become:

- **`cortex sync` says this cortex is too old** → run the command it names — `cortex self-update`, or the install script's from a binary before 1.4.0 — then `cortex sync`, as in format 2, once: still too old afterwards — no newer release to install — the assistant tells the user and goes no further. **It names a missing team's folder** → tell the user the command it names, and go no further — said before the next rule, as such a refusal may name a missing `version` too. **It refuses `cortex.toml` or `cortex.local.toml` for a key, or a missing `version` or `theme`** → run `cortex --version`; below 1.8.0, `cortex self-update`, then `cortex sync`, once each; otherwise, or still refused, tell the user what it refuses, and go no further. *Below 1.8.0* reads the numbers before any `-`: a release candidate or a development build of 1.8.0 has its features (ADR-009), and is not below it — the template says so with examples, `1.8.0-rc.1` and `1.8.0-dev.2`.
- **The team's folder** is `agents` in `cortex.toml`, else `agents/`; the team's file is `cortex.team.toml` there. The cascade's steps name the team's folder where they said `agents/`. The block names no folder, so a rename rewrites no bootstrap.
- **The version** (decision 23): a file's `version`, or the spec's, that differs from what `synced` records → run `cortex sync`, once: a spec still of another version afterwards is told to the user, and the assistant goes no further. **No `synced`**, and a spec that names a version → the same; still none after it — a team's version below 1.8.0 — run `cortex --version`; below 1.8.0, `cortex self-update`, then `cortex sync`, once each; still none, compare as format 2 does, the spec's version with `cortex.toml`'s. Plain equality: no ordering, no git; nothing runs while the machine is in step. **A spec with no version** — `cortex/` a link, a copy whose `.synced` says `"version": null`, or `reads` is `""`, a checkout — has nothing to compare, `synced` or not: the assistant tells the user rather than run a sync that would leave the checkout. The template says the checkout before the version's rule. A copy's `.synced` version is compared, as in format 2.
- **The theme** is `theme` in `cortex.local.toml`, else `cortex.toml`, else the team's file — the team's file's own when it locks `theme`.
- **The assistant never moves a pin of its own accord** (ADR-010 §3.8) — the team's file's and a local `version` as `cortex.toml`'s.

Which template writes the block:

- **The team's bootstrap** follows the team's resolution, during a try too: a format a developer tries is not tried there. `cortex upgrade` writes an untouched one again when its text is not that version's template (ADR-010 §3.5, step 1). Untouched and not that template, it is *behind*: `cortex sync` says so, naming `cortex upgrade` with the team's version — a resume, where a plain `cortex upgrade` would move a project that pins its own version to the binary's — fetching the team's version's templates as it fetches a version; `cortex validate` fetches nothing, and without them says the bootstrap was not checked (ADR-010 §3.5, step 5). A changed block of an older format is not behind, nor is any while the machine reads a checkout. Below 1.8.0 — a pin moved by hand — an untouched format 3 one is behind, which no version before 1.8.0 writes.
- **The developer's own bootstrap** follows the full resolution: `cortex upgrade --local` writes an untouched one again whenever it sets a local version, and `cortex sync` in a project that has a team's file and resolves 1.8.0 or later; `sync --from` writes none. From a team's version of 1.8.0, a move, a resume or `cortex upgrade --team` writes it from the full resolution too — a try's version, and the developer's theme — never from the team's, a move's or a resume's dry run saying so, and never names it among what the team commits; `cortex init` writes it from the full resolution, its theme as read once `cortex.toml` is written included. One the developer changed is kept, said when that version writes another. A move below 1.8.0, and a sync whose team's version is below 1.8.0, write an untouched format 3 one again from the resolved version's template, with the team's theme, as 1.7.0 would; of a changed one, they say it needs 1.8.0. Before 1.8.0, `cortex init` writes it as 1.7.0 does.
- **A team's version below 1.8.0** gets no format 3 team's bootstrap.

### 3.11 The commands

- **`cortex sync`** puts the resolved version in the store, checks its lock (§3.4), records `synced`, writes the developer's own bootstrap (§3.10), and gives the notes.
- **`cortex upgrade`** moves `cortex.toml`'s `version`, as in 1.7.0 (decision 22). In a project that follows a team's file (§3.1) — an uncommitted `cortex.toml` included, at a 2.B root — a move is refused, exit 1, naming `--team` and `--local`, as `--all` skips it; without `VERSION`, or with the team's version or the full resolution's, it resumes — sync, lock, untouched blocks — as ADR-010 §3.5 does on a pinned version, comparing the machine with the full resolution, so that a try is not taken for an upgrade cut short. The overlays and changes are checked against the resolution the moved version belongs to — the full one for `--local`'s, the team's otherwise — and the full resolution reported when it differs. `--all` (ADR-011 §3.5) moves the projects that pin their own version; one that follows the team's file, a developer's `cortex.toml` included, is skipped — *follows the team's 1.8.0* — exit 0.
- **`--team`** writes the team's file — adding `version` to one that has none, for the projects that follow it — its lock, and, when the current project follows it, its untouched team's bootstrap, named as left uncommitted; without a team's file it is refused — the team creates one by a commit in its repository. Before a move, it checks the overlays of the projects that follow the team's file, and says when the current one does not; at the pin, with its lock, nothing moves and no overlay is checked; without its lock, the lock alone is written. `--branch` and `--commit` act in the team's repository; `--commit` commits nothing where the team's file holds more than its version over the one committed — a developer's change, which a plain `--commit` leaves too (ADR-011 §3.5). **`--all --team`** moves each distinct team's file once — a follower whose overlays fail stops the move for all, `--force` overriding (ADR-010 §3.5, step 4) — then runs step 5 in each follower; a project with no team's file, or that does not follow its own, is skipped and said, exit 0; with `--commit`, the team's file and lock are committed there, the followers' blocks left uncommitted and named.
- **`--local`** writes `cortex.local.toml`'s `version` and syncs, writing no committed file (§3.10); at or below the team's version it removes that key, and says so. It refuses `--branch`, `--commit`, `--all` and `--team`, exit 2. No upgrade writes a committed file from the try's version.
- **`cortex validate`** first applies the template's test — each file's `version` against `synced`'s `team`, `project` and `local`; a difference refuses as 1.7.0 does, *run `cortex sync`*, with its exit code — then compares the spec with `reads`, or, without `synced`, with the resolved version. It checks the lock, and warns on a locked key overridden, a missing or left-behind folder, and the team's bootstrap behind.
- **`cortex prune`** keeps, of every project — registered or under a `DIR` — its candidate versions and the version of the spec read now; its loose read covers the team's file.
- **`cortex project list`** gives as `PINNED` the team's version; `--json` adds its origin.

### 3.12 The runtime

- **It asks no git**: its version is the team's. It first compares each file's `version` now with `synced`'s candidates — any difference is a `422` naming `cortex sync` — then takes `pinned`. Without `synced`, a `cortex.toml` version before 1.8.0 is read alone, the team's file neither parsed nor read: no CLI records `synced` for it. Otherwise, `cortex.toml`'s when it pins one, else the team's file's; a team's file whose `version` differs from `cortex.toml`'s is then a `422` naming `cortex sync`. Of `cortex.local.toml` it reads `version` and `synced`, never `spec`, a host's path (ADR-008 §3.6): a `422`, naming the key, only when the version this machine's CLI reads differs from its own. It reads the team's folder `cortex.toml` names, inside the project root it mounts. Without git, it tells an uncommitted `cortex.toml` by `synced` alone — a `pinned` other than `project`, the team's file having won — which records no committedness: at a 2.B root, a version-less `cortex.toml`, or one at the team's version, is read as committed, and a `theme` it sets wins where `cortex config theme --locked` gives the team's file's. The adoption path removes that `theme` (§5); `synced` does not grow a key for it.
- **Its `CORTEX_THEME`** is the environment tier of `theme`, under a lock: the only key where the environment meets a lock in 1.8.0 (decision 13).
- **A `cortex.toml` before 1.8.0 it refuses**, read alone without `synced`: its `422` names `cortex sync` to run when a team's file is there and the file lacks only what the team's may give — mid-adoption, a sync may read the team's file instead of an uncommitted `cortex.toml`, and take its theme — and says the sync refuses the file too otherwise: a theme out of the store or a key it does not know, the team's file gives no sync a way past.

### 3.13 Continuous integration

A project with no team's file keeps the README's recipe of 1.7.0. One with a team's file installs the latest release, reads `cortex config version --locked` — which answers whatever the version — installs the binary of that version, then runs `cortex sync --locked` and `cortex validate --strict`; a team's folder that is its own repository, a submodule, is checked out with the project. **A team's repository checked alone** is checked out at `agents/` in a scratch project whose `cortex.toml` sets nothing the team's file gives. The README gives both.

### 3.14 What other records record

Once this record is implemented, each of these records it in its amendments: ADR-012 (its follow-up taken up, and the reading of another file its §2 and §6 declined adopted for the version and the theme); ADR-001 §3.1, ADR-006 §3.2 and ADR-007 §3.1 (the team's folder, a root of the cascade); ADR-008 §3.3 (`config.toml`), §3.4 (the keys), §3.6 (the runtime's resolution and its `422`s) and §9 (the 2.B pin, and `claude_access` lockable to `false` only, which narrows its *whose value wins*); ADR-010 §3.1 (format 3), §3.2 to §3.5 (the resolved version, the lock's place, `cortex sync` writing the developer's own bootstrap), §3.6 (prune) and §3.8 (the rules); ADR-011 §3.2 (`PINNED`), §3.4 (prune) and §3.5 (`--all`).

## 4. Phases

Execution order **1 → 2a → 2b → 3**, in one release, 1.8.0, which ships with #331 (#328, #329, `cortex upgrade`'s ending outside git, the workspace template's step 4.2).

### Phase 1 — The resolution, the machine's file and `cortex config`

§3.1, §3.2, §3.7, §3.9 and steps 1–2 of §3.8, for the keys of 1.7.0 and the machine's.

- the core's golden outputs are unchanged; a project of 1.7.0 resolves to what 1.7.0 reads, and a 1.8 sync leaves its files byte for byte
- at a 2.B root without a team's file, `sync --locked`, `config --locked` and the runtime read `cortex.toml`'s version; the runtime image, on a host-owned read-only mount, resolves what the host's `cortex config --locked` prints
- a git that refuses counts as no git; a project in a subdirectory of its repository, its `cortex.toml` tracked, is committed; an untracked `cortex.toml` in a repository is not
- the machine's file's `releases_url`, `update_check` and `register` take effect, and each variable overrides its key; an unknown key or a wrong type warns, and `cortex self-update` still runs; an invalid `releases_url` is refused naming the file; an empty `CORTEX_RELEASES_URL` is unset
- a resolved version newer than the binary stops `sync`, `init`, `validate` and `upgrade` without `VERSION`, whatever key a file holds; `cortex upgrade VERSION` still moves down; `config` and `config --locked` print that version and say so; `cortex config KEY` with an unknown key exits 2, and outside a project knows the machine's keys only; a source checkout passes the gate, or the rule is revised

### Phase 2a — The team's file, the version and the lock

§3.3 to §3.5, §3.8 steps 3–4 and §3.11, but for the team's folder.

- a local `1.9.0-rc.1` over a team's `1.8.0` is read, with the note; at `1.8.0` it is said to change nothing; a committed `cortex.toml` at `1.7.0` beside a team's file at `1.8.0` reads `1.7.0`, with the team's file warned of as not read; at a 2.B root, an uncommitted `cortex.toml` at `1.7.0` beside a team's file at `1.8.0` reads `1.8.0`; a pulled bump of a project on 1.8.0, not synced, makes `validate` refuse — *run `cortex sync`*
- the refusal of a version too new names the file and the remedy, `--local` and `--team` included; `cortex upgrade VERSION` refuses when the version resolved after the move is still too new
- a local version writes no lock, warns of none, passes `--strict`; during a try, `cortex upgrade` writes the committed lock from the version it moves, and `sync --locked && validate --strict` passes; `--locked` keeps the local `claude_access`; the team's lock is written under a lock of its folder, and unwritable there warns; the default `agents/` empty warns
- `synced` is written where the team's version is 1.8.0 or later, removed by a move below it, with `reads = ""` after `--from`; a 1.7.0 binary reads the `cortex.local.toml` of a project kept on 1.7.0; a local `version` over a team's 1.7.0 is refused; `cortex init` leaves out of `cortex.toml` the `version` and `theme` the team's file sets; `cortex prune` keeps the team's file's version and a local one
- a team's file whose `version` is below 1.8.0 is refused; `locked` with `version`, `locked`, a key it does not set or twice, or `claude_access = true` is refused; a locked key overridden warns at sync and fails `validate --strict`; `cortex sync --link` against a locked `sync` exits 1; `README.md` and `docs/getting-started.md` say that `claude_access` locks to `false` only, and why
- `--team` without a team's file is refused, and adds `version` to one without; `--all` and `--all --team` skip, exit 0, a project they do not move; `--team` and `--all --team` move a team's file once and step-5 its followers only, a follower's failing overlays stopping all but under `--force`; `--team 1.7.0` is refused before writing; `--all --local` and `--team --local` exit 2; with a local try, `cortex upgrade` and `--all --commit` write no bootstrap from the try's version; `--local` at the team's version removes the local key; a plain upgrade on a version-less `cortex.toml` resumes and refuses a move
- at a 2.B root, a team's file at `1.8.0` and an uncommitted `cortex.toml` at `1.7.0`: `cortex upgrade 1.9.0` is refused, exit 1, naming `--team` and `--local`, as `--all` skips the project

### Phase 2b — Format 3, the runtime and continuous integration

§3.10, §3.12 and §3.13.

- both templates are format 3 with §3.10's rules, the init matrix differing by those lines only; 1.7.0's `cortex upgrade` on an untouched format 3 file writes its own format 2 block and puts the team's part back; a 1.8 sync writes format 3 back over the developer's own file only; a move below 1.8.0 writes an untouched developer's own format 3 bootstrap from the resolved version's template; from 1.8.0, a move, a resume, `--team` and `cortex init` write the developer's own from the full resolution — a try's version, the developer's theme — never named among what the team commits, and before 1.8.0 `cortex init` writes it as 1.7.0 does; a machine reading a checkout has no bootstrap behind
- the runtime reads the team's version, `CORTEX_THEME` under a lock, and a local `version` above it is a `422`; at a 2.B root, an uncommitted `cortex.toml` at `1.7.0` beside a team's file at `1.8.0` reads `1.8.0` as the CLI does, with `synced` fresh and stale, and `1.7.0` with `synced` absent — no 1.8 cortex has synced it yet; the team's file moved, then `cortex.toml` edited to its old value, is a `422` naming `cortex sync`; without `synced`, a `cortex.toml` before 1.8.0 is read alone, beside a team's file at another version or one that cannot be parsed, and from 1.8.0 a team's file whose `version` differs from `cortex.toml`'s is a `422` naming `cortex sync`
- the README's CI recipe passes on a project whose version is the team's file's, and on one of 1.7.0; the docs' adoption path — the team's move announced in `agents/project-context.md`, and at a 2.B root `version` and `theme` removed from `cortex.toml` — brings a teammate on 1.7.0 to the team's version
- in real Claude Code conversations on a 2.B workspace: with a 1.7.0 binary, the assistant runs `cortex self-update`, then `cortex sync`; with a team's file pinning a version the spec is not of, it runs `cortex sync`, then reaches the Prompt Manager's card with the theme the team's file locks; with a 1.8.0 binary and a team's file at a later version, it runs `cortex self-update` as the refusal names it, and syncs once, without a loop; in step, it runs nothing, and after `cortex init --from` too — CONTRIBUTING's loop, run with a source checkout, passes the gate and leaves the checkout in place; with a format 3 block and no `synced`, it compares as format 2 does. The other tools are #137's, for 1.9.0

### Phase 3 — The team's folder

§3.6, and §3.8 for `agents`. The core's cascade takes the team's folder as a layer directory itself, where it took a tier's root and added `agents/`: `resolver`, `catalog`, `validate` and `workspace` change their signature. `agents/roles/prompt-manager.md` and `agents/workflows/README.md` name *the team's folder the bootstrap names*; the runtime's `context.py` and `run.py` read it.

- `agents = "team-agents"` moves the workspace tier, the team's context files, the overlay roots, the team's file and lock, the themes and the runtime's reading; the core's golden outputs are unchanged without it
- `agents` in `cortex.local.toml`, the team's file or the machine's file is refused; in `cortex.toml`, `../x`, `a/../b`, `/x`, `C:\x`, `\\server\x` and `a\b` are refused, and so are the root, `cortex/` and what it holds, a service's folder or one inside a service, a folder holding a project's `cortex.toml`, and `.team`; the default `agents/` never is
- `agents` in an uncommitted `cortex.toml` is refused, the team's folder then `agents/`; a team's folder that is a link leading outside the workspace is read through it, its path checked as written; a missing folder the key names warns at sync and fails `--locked`; a root `agents/` left behind warns; a linked bootstrap is asked of git in its own repository; in a real Claude Code conversation with `agents = "team-agents"`, the assistant reads a role overlay from it

## 5. Consequences

### Positive
- A team pins its version at a 2.B root: one commit in its repository moves every teammate whose binary and bootstrap are of 1.8.0 or later.
- A developer tries a version before the team, without a branch and without touching a committed file, and is told at every sync.
- The machine's settings have a file, `cortex config` says why a command reads what it reads, and a team may rename its folder.

### Negative
- **The first move reaches no teammate alone.** A format 2 bootstrap does not read the team's file: each teammate runs `cortex self-update` once, when their binary is before 1.8.0, and `cortex sync` once, by hand. The docs suggest announcing it in `agents/project-context.md`, which format 2's workspace step 2 reads. At a 2.B root, the adoption path also removes `version` and `theme` from each `cortex.toml` once the team's file sets them: the version would be said to *remove it*, and the theme would silently beat the team's, unless locked.
- **The assistant reads one more file**, the team's — the kind of reading ADR-012 §6 declined for a team's rules, which #137 has not measured outside Claude Code. The IDE modes remove it. A format 3 block kept in a project below 1.8.0 costs each conversation a sync and `cortex --version`, until `cortex upgrade` with the project's version writes it again.
- **A cortex before 1.8.0** ignores the team's file, and refuses a `cortex.toml` using a key of 1.8.0, or lacking `version` or `theme`, as such, not as a binary too old. 1.7.0's `cortex upgrade` writes an untouched format 3 file again in format 2, the team's part kept; a 1.8 sync puts format 3 back in the developer's own file only. Older runtime images refuse such a `cortex.toml`, `422`, and ignore the rest.
- **A team's folder serves one workspace**: a repository shared by several, outside each, waits for #327.

### Neutral
- `cortex.toml` and `cortex.local.toml` keep their names, place and meaning; the variables keep their names and effect.

## 6. Alternatives considered

- **The developer's pin over the team's**, ADR-008 §9's wording, which would hold back on a machine the version the team validated (18); **a lockable `version`**, which would forbid a try (19). Rejected by the maintainer.
- **The lock at a 2.B root**, committed nowhere — rejected by the maintainer (20) — or inside the team's file, mixing what the team writes and what `cortex sync` writes, which ADR-010 §3.2 keeps apart. Rejected.
- **A team's folder outside the workspace, or set per machine** — decision 17's first wording. Rejected by the maintainer (30): the same configuration everywhere; a shared repository is #327's.
- **The configured folder written into Cortex's block**: a rename would rewrite every bootstrap, and a block a team changed could not follow it. Rejected: the templates read the key (§3.10).
- **The templates ordering versions, or guessing committedness from a `.git`** — semver asked of a model, and wrong for a project in a subdirectory of its repository. Rejected: they compare with what `cortex sync` records (23).
- **A new CLI variable per key**, `CORTEX_VERSION` and the like, which would change a committed setting with a shell — the runtime's existing `CORTEX_THEME` stays — and **`releases_url` in a committed tier**, which would let a commit redirect every teammate's downloads (26). Rejected.

## 7. Follow-ups (out of scope for this ADR)

1. **The IDE modes** (#98, its second record) — the resolved values rendered into Cortex's block, which removes §3.10's comparisons.
2. **A team's repository shared by several workspaces** (#327).
3. **The endpoints of local models** (#40), in the machine's file.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.3 the store, §3.4 the project's files, §3.6 the runtime, §7.1 this follow-up, §9 the 2.B pin, `claude_access` and a later binary's checks
- [ADR-010](ADR-010-lock-and-upgrade.md) — §3.1 the format, §3.2 to §3.6 the lock, `sync`, `validate`, `upgrade`, `prune`, §3.8 the bootstrap's rules
- [ADR-011](ADR-011-project-registry.md) — §3.2, §3.4, §3.5 and its committed test
- [ADR-007](ADR-007-cortex-core.md) §3.1, [ADR-006](ADR-006-workspace-shareable-repo.md) §3.2, [ADR-001](ADR-001-layered-overrides.md) §3.1 — the cascade's roots; [ADR-012](ADR-012-bootstrap-block.md) — Cortex's block, §5 an older cortex and a later format
- Issues: [#98](https://github.com/xod-labs/cortex/issues/98), [#327](https://github.com/xod-labs/cortex/issues/327), [#137](https://github.com/xod-labs/cortex/issues/137), [#40](https://github.com/xod-labs/cortex/issues/40); [#331](https://github.com/xod-labs/cortex/pull/331)

## 9. Amendments

The record was *Accepted*, not yet implemented, when these were made: the body above already says each. They are listed against the text Bluspark's counter-review read, at a0227a1.

### The counter-review of 2026-11-07

- **A plain move is refused in a project that follows a team's file** (§3.11, decision 22). At a 2.B root with a team's file at 1.8.0 and an uncommitted `cortex.toml` at 1.7.0, `--all` said *follows the team's 1.8.0*, while `cortex upgrade 1.9.0` moved the uncommitted `cortex.toml` — a try not called one, with a lock of its own. Such a move is now refused, exit 1, naming `--team` and `--local`, as `--all` skips the project; the resume is unchanged. The maintainer's call narrows decision 22 at a 2.B root with a team's file only.
- **`agents` is read from a committed `cortex.toml` only** (§3.6, §3.8 step 1, decision 30). At a 2.B root, `cortex.toml` is committed nowhere, so `agents` there was one machine's; it is refused, and the team's folder is `agents/`.
- **A team's folder may be a symbolic link leading outside the workspace** (§3.6), the default `agents/` included: Cortex checks the path as written, resolves no link, and names no path outside; paths compare as written.
- **The runtime without `synced`** (§3.12): a team's file whose `version` differs from `cortex.toml`'s is a `422` naming `cortex sync`.
- **The adoption path at a 2.B root** (§5) removes `version` and `theme` from `cortex.toml` once the team's file sets them: a theme left there would silently beat the team's.
- **Phase 2 is split** into 2a — the team's file, the version, the lock and the upgrade rules — and 2b — format 3, the runtime and continuous integration; the contract is unchanged, the order 1 → 2a → 2b → 3.

### While the phases were implemented

- **2026-11-07, phase 1** (§3.8, step 2). The gate lets a source checkout or a development build — `0.0.0-dev`, `0.0.0-dev.N`, which a dispatch of the binary workflow builds — through, where the text named a source checkout only and left the rule to be revisited: without it, such a build refused what 1.7.0 accepted — `cortex validate` from source of a project pinned to a release, and a build's own tests of a project pinned to 1.0.0. `cortex upgrade` with no `VERSION` still refuses it a newer pin, as 1.7.0 did: passing, it would move the project down to `0.0.0-dev`.
- **2026-11-07, phase 2a** (§3.8, step 3). A source checkout's or a development build's version — `0.0.0-dev`, `0.0.0-dev.N` — counts as 1.8.0 or later for what this record adds, as it passes the gate of step 2: a project such a build makes pins that version, and would otherwise read no team's file, no try and no `synced` — CONTRIBUTING's loop, and a development build's own tests, could not exercise them.
- **2026-11-08, phase 2b** (§3.12) — *the maintainer's confirmation pending*. Without `synced`, a `cortex.toml` version before 1.8.0 is read alone, the team's file neither parsed nor read, where the counter-review of 2026-11-07 made any difference with the team's file a `422` naming `cortex sync`. Below 1.8.0 that `422` could never be cleared: a committed pin before 1.8.0 is the team's version, and no CLI records `synced` for it (§3.8, step 3), so `cortex sync` left the runtime refusing the project for good — 1.7.0's runtime ran it. At a 2.B root with no `synced` yet, it reads what the 1.7.0 binary syncs there, until the first 1.8 sync records `synced`. From 1.8.0 the `422` stands.
