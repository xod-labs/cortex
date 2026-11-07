# ADR-013 — The configuration cascade: the team's settings, the machine's, and where each value comes from

- **Status:** Proposed
- **Date:** 2026-11-07 (proposed, with the maintainer's arbitrations of 2026-11-06 and 2026-11-07, §2)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `core/cortex_core/project.py` (the grammar of the team's file and the machine's, the new keys, the resolution), `core/cortex_core/resolver.py`, `catalog.py`, `validate.py` and `workspace.py` (the team's folder), `cli/cortex_cli/` (`config`, `sync`, `upgrade`, `init`, `validate`, `registry`, `prune`, `store`, `self_update`, `main`, and a new `cortex config` command), `runtime/cortex_runtime/base.py`, `templates/bootstrap-instructions*.md` (format 3), `README.md` (its CI recipe and its table of variables), `docs/getting-started.md`, `docs/extending-layers.md` (the Windows CI lines)
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) §3.3 (`~/.cortex/config.toml`, reserved), §3.4, §3.6, §7.1 (this follow-up) and §9 · [ADR-010](ADR-010-lock-and-upgrade.md) §3.1 to §3.6 and §3.8 · [ADR-011](ADR-011-project-registry.md) §3.2, §3.4 and §3.5 · [ADR-006](ADR-006-workspace-shareable-repo.md) §3.2 · [ADR-001](ADR-001-layered-overrides.md) §3.1 · [ADR-012](ADR-012-bootstrap-block.md) · roadmap epic [#98](https://github.com/xod-labs/cortex/issues/98), first of its two records · [#327](https://github.com/xod-labs/cortex/issues/327) · [#137](https://github.com/xod-labs/cortex/issues/137) · [#40](https://github.com/xod-labs/cortex/issues/40)

---

## 1. Context

ADR-008 left the configuration cascade to #98 (§7.1): four tiers — organisation ▸ project ▸ project-local ▸ machine — of which `cortex.toml` and `cortex.local.toml` are the two in the middle, and `~/.cortex/config.toml` reserved for the machine (§3.3). Four gaps have shown since:

- **A team that does not commit its root cannot pin its version.** At a workspace root that is no git repository — flavor 2.B of ADR-006 — `cortex.toml` is committed nowhere, and pinning is per developer (ADR-008 §9). Moving Bluspark's workspace to 1.7.0 on 2026-11-07 moved one machine; each teammate stays on their own version until they move it. ADR-008 §9 left the answer here: a pin in the team's `agents/` repository.
- **The machine's settings are variables only** — `CORTEX_RELEASES_URL`, `CORTEX_NO_UPDATE_CHECK`, `CORTEX_NO_REGISTER`, and `CORTEX_HOME` for the home itself. Nothing writes them down, and nothing says which one is set when a command behaves otherwise than expected.
- **The team's folder is `agents/`, fixed.** The resolver, the capability catalog, `cortex validate` and the service scan all read `{project_root}/agents/`. A team whose overlays serve several workspaces clones them at `agents/` in each.
- **Trying a version is moving it.** In a committed project, a developer tries a newer version on a branch. At a 2.B root, nothing tells a developer's try from the team's choice.

On 2026-11-06 the maintainer cut #98 in two: this record, then the IDE modes, which #137 will validate.

## 2. Decision

**Cortex reads its settings from five places — the environment, the developer's `cortex.local.toml`, the project's `cortex.toml`, the team's `cortex.team.toml` in the team's folder, and the machine's `config.toml` — and for each key the most specific value wins. Two exceptions: for `version`, the highest wins; a key the team's file locks takes the team's value. The project names the team's folder, `agents/` by default. `cortex config` says each value and where it comes from.**

- **The team's file**, `cortex.team.toml` in the team's folder, gives defaults to every project that reads the folder, and imposes the keys it lists in `locked`. It is committed in the team's repository. At a 2.B root, it is the team's pin.
- **`version`: the highest wins** between the team's version and the developer's. A developer may read a version above the team's to try it before proposing it, and is told so at every sync.
- **The lock** sits beside the file whose `version` won: `cortex.team.lock` in the team's folder, or the project's `cortex.lock` as in ADR-010. `cortex.local.toml`'s version is not locked.
- **The machine's file**, `~/.cortex/config.toml`, holds the machine's settings; today's variables override it.
- **`agents`**, a key of `cortex.toml` and `cortex.local.toml`, names the team's folder: a path relative to the project root, which may lead outside it.
- **`cortex config`** prints each setting, its value and its origin.
- **What this record adds is read only by a project that resolves 1.8.0 or later**, and a binary too old for the resolved version says so before it reads any other key. The templates move to format 3.
- **Nothing of 1.7.0 changes meaning**: every file, key and variable reads as before, and everything new is optional. The record ships in 1.8.0, a minor version.

Decided with the maintainer on 2026-11-06 and 2026-11-07, numbered as the arbitrations of #98 were:

- **12** — an organisation policy, versioned in the team's repository, that gives defaults and locks only the keys it lists;
- **13** — the most specific value wins — local, project, team, machine — and the environment goes over all of them, except on a locked key;
- **14** — the keys distributed per tier, the machine's in `~/.cortex/config.toml`;
- **15** — at a 2.B root, the team pins its version in the team's folder;
- **16** — a `cortex config` that says where each value comes from;
- **17** — the team's folder set per project, a path relative to the workspace root that may lead outside it; a repository named by its git URL waits for #327;
- **18** — for `version`, the highest wins between the team's pin and the developer's: a team's pin above the developer's was validated by the team's process, and a developer may try a higher one to validate it before proposing it;
- **19** — `version` is never lockable: moving it stays the team's matter, settled by its own process;
- **20** — the lock beside the team's pin, in the team's folder, which follows the configured name.

Out of scope: the IDE modes and the rendering of resolved prompts (#98, its second record); a team's repository named by git URL and pinned in the lock (#327); the endpoints of local models (#40); the runtime's own variables but `CORTEX_THEME` (§3.12).

## 3. Detailed contract

### 3.1 The tiers

| Tier | Where | Written by |
|---|---|---|
| environment | the variables of §3.7 | the developer's shell, a CI |
| local | `cortex.local.toml`, at the project root — never committed | the developer; `cortex sync` for `spec` and `claude_entry`; `cortex upgrade --local` for `version` |
| project | `cortex.toml`, at the project root | `cortex init`, `cortex upgrade`, the team |
| team | `cortex.team.toml` in the team's folder (§3.6), committed in the team's repository | the team; `cortex upgrade --team` |
| machine | `config.toml` in the Cortex home — `$CORTEX_HOME`, `~/.cortex` by default | the developer |

For each key, the value is that of the first tier that sets it, from the top of the table down; a key no tier sets takes its default. Two exceptions: a key the team's file locks (§3.5), and `version` (§3.3).

**Committed** means, of `cortex.toml`, that git tracks it — ADR-011 §3.5's test. Without `git` on `PATH`, a `.git` at or above the project root counts as committed. An uncommitted `cortex.toml` — at a 2.B root, or untracked — is the developer's own (ADR-010 §3.8).

### 3.2 The keys, tier by tier

| Key | Environment | Local | Project | Team | Machine | Default |
|---|---|---|---|---|---|---|
| `version` | — | ✓ | ✓ | ✓ | — | none: required in `cortex.toml` or the team's file |
| `theme` | runtime only (§3.12) | ✓ | ✓ | ✓ | — | none: required in `cortex.toml` or the team's file |
| `sync` | — | — | ✓ | ✓ | — | `store` |
| `claude_access` | — | ✓ | ✓ | ✓ | — | unset: Claude Code's settings left alone |
| `agents` | — | ✓ | ✓ | — | — | `agents` |
| `locked` | — | — | — | ✓ | — | none |
| `releases_url` | `CORTEX_RELEASES_URL` | — | — | — | ✓ | GitHub's releases of `xod-labs/cortex` |
| `update_check` | `CORTEX_NO_UPDATE_CHECK` | — | — | — | ✓ | `true` |
| `register` | `CORTEX_NO_REGISTER` | — | — | — | ✓ | `true` |

- **A key a tier does not take is an error in that tier's file**, as an unknown key is (ADR-008 §3.4) — the machine's file aside (§3.7).
- **`releases_url`, `update_check` and `register` are the machine's.** A committed file never decides where a machine downloads from, nor whether it keeps its registry: a commit to a team's repository would otherwise send every teammate's downloads to another host.
- **`spec` and `claude_entry`** stay `cortex sync`'s, in `cortex.local.toml` only. **`CORTEX_HOME`** stays a variable only: it says where the machine's file is.

### 3.3 `version`: the highest wins

- **The team's version** is the highest `version` of the team's file and of a committed `cortex.toml`. **The developer's version** is the highest of `cortex.local.toml`'s and of an uncommitted `cortex.toml`'s.
- **The resolved version** is the higher of the two, in Semantic Versioning's order — a pre-release below its release, `1.9.0-rc.1` below `1.9.0`. On a tie the team's wins, and within the team's, a committed `cortex.toml` over the team's file: a project of 1.7.0 that adds a team's file at its own version keeps its own lock.
- **`version` is never lockable** (§3.5).
- **Ahead.** When the developer's version is above the team's — when there is one — `cortex sync`, `cortex upgrade` and `cortex validate` say so every time, and, of `cortex.local.toml`'s, that it is not locked:

  ```text
  note: Cortex 1.9.0-rc.1 is read — cortex.local.toml's, above the team's 1.8.0 (agents/cortex.team.toml). It is not locked: its release was checked against its SHA256SUMS when downloaded.
  ```

- **Caught up.** A developer's version at or below the team's changes nothing, and the same commands say so — *remove it*. No command edits a key of the developer's unasked.
- **Without a team's file** and without `version` in `cortex.local.toml`, the resolved version is `cortex.toml`'s, as in 1.7.0.

### 3.4 The lock

| The resolved version comes from | Its lock |
|---|---|
| a committed `cortex.toml` | `cortex.lock` at the project root — ADR-010 §3.2, unchanged |
| the team's file | `cortex.team.lock` in the team's folder, committed in the team's repository, with `cortex.lock`'s grammar |
| an uncommitted `cortex.toml` | `cortex.lock` at the project root, the developer's own, committed nowhere — as ADR-010 §3.2 has it at a 2.B root |
| `cortex.local.toml` | none |

- **`cortex sync` applies ADR-010 §3.3's table to that lock**, and reads no other. Its *commit it* names where: *cortex.team.lock written — commit it in agents/*. A lock that cannot be written there stops the sync before anything moves, naming the folder.
- **A version from `cortex.local.toml`** was checked against its release's `SHA256SUMS` when downloaded (ADR-008 §3.3), and is not checked again. `cortex validate` gives no lock warning for it, and `--strict` passes.
- **`--locked`** ignores `cortex.local.toml`'s `version`, and reads every other key as usual, `agents` included: the version is the team's, or, where there is none, `cortex.toml`'s; its lock must be there and name it (ADR-010 §3.3). A team's folder that `agents` names, or that git tracks in the project — a submodule — and that is missing or empty, an uninitialised submodule, is an error.
- **`cortex validate`** compares the spec with the resolved version, and gives ADR-010 §3.4's warnings against the lock of the file that won.

### 3.5 The team's file

```toml
# The team's Cortex settings, for every project that reads this folder (ADR-013). Committed in the team's repository.
version = "1.8.0"
theme = "h2g2"
claude_access = false
locked = ["claude_access"]
```

- **Where:** `cortex.team.toml` in the team's folder (§3.6). Optional: without it, there is no team tier.
- **Its keys:** `version`, `theme`, `sync`, `claude_access` and `locked`, none required. The grammar is the core's: an unknown key is an error.
- **`locked`** lists keys the file sets, each once. A key it does not set, `version`, `locked` itself, or a key twice is refused.
- **`claude_access` is lockable to `false` only.** A team may forbid Claude Code to read the spec without asking; it never grants that permission over a developer who declined it, whose choice ADR-008 §9 lets win.
- **A locked key that a tier above sets otherwise** takes the team's value. `cortex sync` and `cortex validate` warn, naming the file, the key and both values; `cortex validate --strict` fails. **A flag** that sets a locked key to another value — `cortex sync --link`, `cortex init --theme`, `--claude-access` — is refused, exit 2, naming the lock.
- **It is read in every layout** that has a team's folder: at a 2.B root it is the only committed tier; in a committed project, a second one, for teams that share a folder between projects.

### 3.6 The team's folder: `agents`

```toml
agents = "../bluspark-agents"    # cortex.toml — relative to the project root
```

- **Where it is set:** `agents` in `cortex.local.toml`, relative or absolute — where this machine has its clone — else in `cortex.toml`, relative, with `/` separators. Not in the team's file, which is in the folder the key names; not in the machine's file nor the environment, which are not the project's. **Default:** `agents`, the folder of ADR-006.
- **Refused:** in `cortex.toml`, an absolute path of any platform — `/x`, `C:\x`, `\\server\x`. Anywhere, a folder that is the project root or one of its ancestors, that lies inside `cortex/` or the store, or that holds a project's `cortex.toml` — another project's pin would become this one's. Folders compare as the file system compares them: in any case on macOS and Windows, through links.
- **What follows the folder:** the workspace tier of the cascade (ADR-001 §3.1), the team's context files (ADR-006 §3.2), the overlay roots `cortex validate` checks, the folder the service scan leaves out, the team's file and its lock, and the project's own themes. A service's `{service}/agents/` and the `Base:` headers stay as they are.
- **Missing:** the default `agents/` missing is silent, as in 1.7.0. A folder the key names, missing, is a warning at every sync — its overlays, context and settings are not read — and an error under `--locked` (§3.4). **Left behind:** when the key names another folder while the root's `agents/` still holds overlays or the team's context files, the commands warn that those are not read.
- **A committed file directs reads and writes outside the project**: `cortex sync` writes the team's lock there. The refusals above keep it out of the project's ancestors, the spec and other projects; what is left is the team's own repository, which the project names in a commit.

### 3.7 The machine's file and the environment

```toml
# This machine's Cortex settings, for every project of this user (ADR-013).
releases_url = "https://mirror.example.com/cortex/releases"
update_check = false
register = false
```

- **Where:** `config.toml` in the Cortex home — the file ADR-008 §3.3 reserved. Optional; no command writes it.
- **Lenient:** an unknown key, or a file that cannot be read, is a warning naming it, and the rest is read; `cortex self-update` never fails on it. `releases_url` is checked as `CORTEX_RELEASES_URL` is — https, http to this machine only — and a refusal names the file.
- **The variables override it**, as they behave in 1.7.0: `CORTEX_RELEASES_URL` gives `releases_url`, an empty one counting as unset; `CORTEX_NO_UPDATE_CHECK` and `CORTEX_NO_REGISTER`, set to a value that is not empty, make `update_check` and `register` false. `cortex self-update`'s note on the host it downloads from names the variable or the file that set it.
- **Read outside a project too**, by `cortex self-update`. The install scripts read their own variables, `CORTEX_RELEASES_URL` among them, never this file.
- **Not tied to a version**: a machine's settings are the binary's, whatever version a project resolves (§3.8).
- **The environment over a locked key** — arbitration 13 — concerns no key of the CLI in 1.8.0: the team's file locks none of the machine's keys. It applies to the runtime's `CORTEX_THEME` (§3.12), and to keys a later record gives both.

### 3.8 Which version reads what

What this record adds is read only by a project that resolves **1.8.0 or later**: the team's file, `version` in `cortex.local.toml`, `agents`, and a `cortex.toml` without `version` or `theme`. A project that resolves an earlier version reads with that version's templates, whose rules know none of them.

1. **Every file's `version`, and `agents`, are read loosely first** — any other key passed over — and the version resolved (§3.3).
2. **A resolved version newer than the binary** is refused for that, before any key is checked: *this cortex is too old — `cortex self-update`*, which the bootstrap's rule acts on (ADR-010 §3.8). So is a key a later version adds, from 1.8.0 on.
3. **Then every file's keys are checked**: a key, file or omission of this record, in a project that resolves a version before 1.8.0, is refused, naming 1.8.0.
4. **`cortex init`** writes `version` and `theme` in `cortex.toml` whenever the resolved version's templates predate 1.8.0, and otherwise not those the team's file sets: a `version` written from the binary would put a developer ahead of the team on their first init.

### 3.9 `cortex config`

```text
cortex config [KEY] [--locked] [--json]
```

```text
version        1.9.0-rc.1    cortex.local.toml — above the team's 1.8.0 (agents/cortex.team.toml)
theme          h2g2          agents/cortex.team.toml — locked
sync           store         default
claude_access  false         agents/cortex.team.toml — locked
agents         agents        default
releases_url   https://github.com/xod-labs/cortex/releases    default
update_check   true          default
register       false         CORTEX_NO_REGISTER
```

- **Each key**, its value and its origin: a file, as the project root names it, a variable, or *default*. A locked key says so; `version` names the team's when the developer's is above it.
- **`KEY`** prints that key's value alone — an unknown key exits 2. **`--locked`** resolves as `cortex sync --locked` does (§3.4). **`--json`** prints an object: each key's value, origin, the values it overrode, and whether it is locked.
- **Outside a project**, the machine's keys only. It writes nothing; a refused file exits 1, naming the file and the key.

### 3.10 The bootstrap: format 3

The templates move to **format 3**: they ask the assistant to read a file it did not read before, which ADR-010 §3.1 raises the format for. The end marker and the two parts of ADR-012 carry over unchanged. Their first rules become:

- **The team's folder** is `agents` in `cortex.local.toml`, else in `cortex.toml`, else `agents/`; the team's file is `cortex.team.toml` there. The cascade's steps name the team's folder where they named `agents/`. Cortex's block names no folder, so a machine's own `agents` changes nothing in a committed file.
- **The version:** a spec of another version than the highest `version` of `cortex.local.toml`, `cortex.toml` and the team's file → run `cortex sync`, and go no further until it succeeded.
- **The theme** is `theme` in `cortex.local.toml`, else in `cortex.toml`, else in the team's file — the team's file's own when it locks `theme`.
- **The assistant never moves a pin of its own accord** (ADR-010 §3.8): the team's file and `cortex.local.toml`'s `version` as `cortex.toml`'s. Asked, it runs `cortex upgrade`, `--team` or `--local`, the dry run first.

**The block is always written from the resolved version's template.** In a committed project, `cortex upgrade` writes an untouched block again when its text is not the resolved version's, as for a pin moved by hand (ADR-010 §3.5, step 1). Where `cortex.toml` is uncommitted, the file is the developer's own, and `cortex sync` writes it again too: a 2.B workspace follows the team's move at the next sync.

### 3.11 The commands

- **`cortex sync`** puts the resolved version in the store, checks it against the lock of §3.4, and says the notes of §3.3, §3.5 and §3.6.
- **`cortex upgrade`** moves `cortex.toml`'s `version`, as in 1.7.0; when that is not the version resolved — `cortex.toml` at `1.8.0`, the team's at `1.9.0` — it says which one is read.
- **`--team`** writes the team's file and its lock, and the current project's untouched block from the resolved version; it says which version is read when the team's does not win. `--branch` and `--commit` act in the repository the team's folder is in.
- **`--local`** writes `cortex.local.toml`'s `version` and syncs; it writes no bootstrap, which is committed. A version at or below the team's is refused — it would change nothing — and so are `--branch` and `--commit`, exit 2.
- **`--all`** (ADR-011 §3.5) moves the projects that hold their own pin; one whose version comes from the team's file is reported *follows the team's 1.8.0*. **`--all --team`** moves each distinct team's file once, and checks the overlays of every registered project that reads it against the target before writing.
- ADR-010 §3.5's rules on the target apply to the version the moved file pins — the resolved one when it pins none.
- **`cortex validate`** checks the spec against the resolved version, and warns on a locked key overridden, a missing or left-behind folder (§3.4 to §3.6).
- **`cortex prune`** keeps, of every project — registered or under a `DIR` — each candidate version — the team's file's, `cortex.toml`'s, `cortex.local.toml`'s — and the version of the spec read now. Its loose reading of a project it cannot fully read covers the team's file too.
- **`cortex project list`** gives as `PINNED` the team's version, or, where there is none, `cortex.toml`'s, as in 1.7.0; `--json` adds its origin. `READS` is unchanged.

### 3.12 The runtime

- It reads the version and the theme through the core's resolution, from the team's file and `cortex.toml`, never from `cortex.local.toml`, whose paths are the host's (ADR-008 §3.6).
- **Its `CORTEX_THEME`** is the environment tier of `theme` there, under a lock: a team's file that locks `theme` wins over it.
- **A team's folder outside the project root** is refused, `422`, naming the folder: the runtime's container mounts the project and the store, nothing beside them. Mounting it is a follow-up (§7).

### 3.13 Continuous integration

The README's recipe installs the binary of the version the project resolves without `cortex.local.toml`. That version may now be the team's file's: the job installs a binary of 1.8.0 or later, reads `cortex config version --locked`, installs the binary of that version, then runs `cortex sync --locked` and `cortex validate --strict`. A project whose `cortex.toml` pins its version keeps the recipe of 1.7.0. A team's folder that is its own repository is checked out where the project names it.

**A team's repository checked alone** — its own CI — uses a scratch project beside its checkout, whose `cortex.toml` holds `agents = "../the-team-repository"` and nothing else the team's file gives. The README gives its lines.

### 3.14 What stays as it is, and what other records record

- A project with no team's file, no `version` in `cortex.local.toml` and no `agents` key resolves as in 1.7.0, and so does every variable. An unknown key in a project's file is an error (ADR-008 §3.4); the lock's grammar, format 1; the store; the end marker of ADR-012; the services' folders; the `Base:` headers.
- Once this record is implemented, each of these records it in its amendments: ADR-001 §3.1 and ADR-006 §3.2 (the team's folder); ADR-008 §3.4 (the keys), §3.6 (the runtime's version and its `422`) and §9 (the 2.B pin, and `claude_access` lockable to `false` only); ADR-010 §3.1 (format 3), §3.2 to §3.5 (the resolved version, the lock's place, `cortex sync` writing the bootstrap at an uncommitted root), §3.6 (prune) and §3.8 (the rules); ADR-011 §3.2 (`PINNED`), §3.4 (prune) and §3.5 (`--all`).

## 4. Phases

Execution order: **1 → 2 → 3 → 4**. All ship in one release, 1.8.0: new keys and files, every one optional, nothing a project must change.

### Phase 1 — The resolution, the machine's file and `cortex config`

§3.1, §3.2 and §3.7 to §3.9, for the keys that exist today and the machine's.

Acceptance criteria:

- the core resolves each key with its origin, for a project and outside one; the CLI reads it from there; a project of 1.7.0 resolves to what 1.7.0 reads, key by key
- the machine's file gives `releases_url`, `update_check` and `register`, the variables over it; an empty `CORTEX_RELEASES_URL` is unset; an unknown key or a malformed file is a warning, and `cortex self-update` still runs; an `http://` URL to another host is refused, naming the file
- every file's `version` is read before its keys: a version newer than the binary is refused as too old, whatever key the file holds
- `cortex config`, `KEY` and `--json` give each value and its origin; an unknown `KEY` exits 2; a refused file exits 1

### Phase 2 — The team's file: the version, the lock, the defaults and format 3

§3.3 to §3.5, §3.10 to §3.13, and the gate of §3.8 for the team's file and `version` in `cortex.local.toml`.

Acceptance criteria:

- the resolved version is the higher of the team's and the developer's, a pre-release below its release, ties as §3.3 says; the notes of §3.3 are said at every sync, upgrade and validate
- the lock is written and checked beside the file that won; a local version writes none, warns of none, and passes `--strict`; `--locked` ignores the local `version` only, and fails on a missing or empty team's folder
- the team's file, a local `version` and a version-less `cortex.toml` are refused, naming 1.8.0, in a project that resolves an earlier version
- `locked` refuses `version`, `locked`, a key it does not set, a key twice, and `claude_access = true`; a locked key overridden is warned at sync and fails `validate --strict`; a flag against a lock exits 2
- `cortex upgrade --team`, `--local`, `--all` and `--all --team` do what §3.11 says; `--local` refuses a version at or below the team's, and `--branch` and `--commit`
- `cortex init` writes `version` and `theme` as §3.8 says; `cortex prune` keeps every candidate version; `cortex project list` gives the team's version as `PINNED`
- both templates are format 3 with the rules of §3.10; the init matrix differs by those lines only; `cortex sync` writes again an untouched block at an uncommitted root
- the runtime reads the committed tiers, `CORTEX_THEME` under a lock
- the README's CI recipe passes on a project whose version is the team's file's, and on one of 1.7.0
- in a real Claude Code conversation, on a 2.B workspace whose team's file pins a version the spec is not of, the assistant runs `cortex sync`, then reaches the Prompt Manager's card with the theme the team's file locks. The other tools are #137's, for 1.9.0.

### Phase 3 — The team's folder

§3.6, and the gate of §3.8 for `agents`.

The core's cascade takes the team's folder as a layer directory itself, where it took a tier's root and added `agents/` to it: `resolver`, `catalog`, `validate` and `workspace` change their signature accordingly.

Acceptance criteria:

- `agents` moves the workspace tier, the team's context files, the overlay roots, the service scan's exclusion, the team's file and lock, and the project's own themes; a path leading outside the root works
- an absolute `agents` in `cortex.toml` is refused, on every platform's form; so are the root, an ancestor, a folder inside `cortex/` or the store, and one holding a project's `cortex.toml`, compared as the file system compares them
- a missing folder the key names is warned at sync and fails `--locked`; a root `agents/` left behind is warned
- the runtime refuses a team's folder outside the project root, `422`, naming it

### Phase 4 — The fixes that ride along

Older than this record, each found on the way to 1.8.0; each criterion fails before its fix.

- **#328**: with `cortex.toml` removed, `cortex init --from co --link` over the link a `--from` sync made goes through instead of saying Cortex did not make it.
- **#329**: `cortex init --force` without `--workspace`, over a bootstrap whose marker says `layout=workspace`, writes the workspace's template, or refuses naming `--workspace` — whichever the issue settles; tests kill the mutants WS2 to WS4 of its comment; ADR-001 §3.5 gets the line of amendment ADR-012 §9 owes it.
- **`cortex upgrade`'s ending**, at a root whose `cortex.toml` is uncommitted, no longer says *Commit cortex.toml, cortex.lock*.
- **The workspace template's step 4.2** lists the `@alias` values from the services table below the end marker, else from the services' `project-overview.md`.

## 5. Consequences

### Positive
- A team pins its version at a 2.B root: one commit in its repository moves every teammate, whose assistant syncs after the pull, and whose bootstrap follows.
- A developer tries a version before the team, without a branch and without touching a committed file, and is told at every sync.
- The machine's settings have a file, and `cortex config` says why a command reads what it reads.
- A team's folder can serve several workspaces, cloned once beside them.

### Negative
- **The assistant reads one more file**, the team's, to compare versions and find the theme — the kind of reading ADR-012 §6 declined to add for a team's rules, and which #137 has not measured outside Claude Code. The IDE modes, which render the resolved values, remove it.
- **A cortex before 1.8.0 does not read the team's file**, and refuses a `cortex.toml` that uses a key of 1.8.0 as an unknown or missing key, not as a binary too old. A teammate who has not updated keeps their own version, unwarned, until they run `cortex self-update` — which moving the team's pin past 1.7 calls for, said by the team. Runtime images before 1.8.0 refuse those files, `422`.
- **A project that shares the team's folder cannot stay behind it**: the team's move moves every project that reads the folder, its overlays included. `--all --team` checks them before writing; open arbitration 4 asks whether a committed project may pin lower.
- **Two locks may exist** in a committed project whose team's file pins above `cortex.toml`: only the lock of the file that won is read, and the other goes stale until `cortex upgrade` brings `cortex.toml` level.

### Neutral
- `cortex.toml` and `cortex.local.toml` keep their names, their place and their meaning; the variables keep their names and their effect.
- Format 3 is read by a cortex before 1.8.0 as a later format: kept, with a note, as ADR-012 §5 describes for format 2.

## 6. Alternatives considered

- **The developer's pin over the team's**, the most specific winning for `version` too — ADR-008 §9's wording. A developer who has not moved would hold the version the team validated back on their machine. Rejected by the maintainer (18).
- **A lockable `version`.** It would forbid trying a version before proposing it. Rejected by the maintainer (19).
- **The lock at the root of a 2.B workspace.** Committed nowhere, it guarantees the team nothing. Rejected by the maintainer (20).
- **The lock inside the team's file.** It would mix what the team writes and what `cortex sync` writes, which ADR-010 §3.2 keeps apart. Rejected.
- **The configured folder written into Cortex's block.** The block is committed, and `agents` may be one machine's: the block would follow whoever last ran `cortex upgrade`. Rejected: the templates read the key themselves (§3.10).
- **A new CLI variable for every key**, `CORTEX_VERSION` and the like. A committed setting would change with a shell, unseen by the team. Rejected: the CLI's environment keeps the machine's keys; the runtime's existing `CORTEX_THEME` stays.
- **`releases_url` in the committed tiers.** A commit to the team's repository would redirect every teammate's downloads. Rejected.
- **A team's repository named by git URL**, fetched by `cortex sync` and pinned in the lock. Its own record, #327, later.

## 7. Follow-ups (out of scope for this ADR)

1. **The IDE modes** (#98, its second record) — the resolved values rendered into Cortex's block, which removes the comparisons §3.10 asks of the assistant.
2. **A team's repository shared by git URL** (#327).
3. **The runtime mounting a team's folder outside the project root**, which 1.8.0 refuses (§3.12).
4. **The endpoints of local models** (#40), in the machine's file.
5. **`cortex config set`**, should editing the files by hand prove error-prone.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.3 the store and `config.toml`, §3.4 the project's files, §3.6 the runtime, §7.1 this follow-up, §9 the 2.B pin and `claude_access`
- [ADR-010](ADR-010-lock-and-upgrade.md) — §3.1 the marker's format, §3.2 to §3.6 the lock, `sync`, `validate`, `upgrade` and `prune`, §3.8 the bootstrap's rules
- [ADR-011](ADR-011-project-registry.md) — §3.2 `cortex project`, §3.4 `prune`, §3.5 `upgrade --all` and the committed test
- [ADR-006](ADR-006-workspace-shareable-repo.md) — the team tier, `agents/`
- [ADR-012](ADR-012-bootstrap-block.md) — Cortex's block and the end marker
- Issues: [#98](https://github.com/xod-labs/cortex/issues/98), [#327](https://github.com/xod-labs/cortex/issues/327), [#137](https://github.com/xod-labs/cortex/issues/137), [#40](https://github.com/xod-labs/cortex/issues/40), [#328](https://github.com/xod-labs/cortex/issues/328), [#329](https://github.com/xod-labs/cortex/issues/329)

## 9. Amendments

None yet.

## Open arbitrations

For the maintainer, before acceptance; each answer is folded into the text above, and this section removed.

1. **The team's file's name.** (b) `cortex.team.toml` beside `cortex.team.lock`, as written; (a) `cortex.toml` and `cortex.lock`, as arbitration 15 named it. Under (a), a command run inside a team's folder outside the project root takes the team's file for a project's; `agents = "../other-project"` would adopt another project's pin, which §3.6 could not refuse by the file's name; a 1.7 workspace holding an `agents/cortex.toml` would change meaning; and a cortex before 1.8.0 may treat `agents/` as a project. **Recommended: (b)** — it departs from the name arbitration 15 gave, not from its decision: the team pins in its folder, its lock beside the pin.
2. **What `cortex upgrade` moves by default once a team's file exists.** (a) `cortex.toml`, as in 1.7.0, with `--team` and `--local` (§3.11); (b) the team's file whenever it pins `version`. **Recommended: (a)** — the default does not change with the project's files, nor within the minor version; at a 2.B root it is the developer's try, `--team` the proposal to the team.
3. **The bootstrap and the team's file.** (a) The templates find the team's folder through `cortex.local.toml`, then `cortex.toml`, and read the team's file there (§3.10); (b) `cortex sync` writes the resolved values in `cortex.local.toml`, and the templates read only that — but a team's move is then seen at the next sync, not at the next conversation. **Recommended: (a)** — the rule exists to catch a teammate's move (ADR-010 §3.8), and the IDE modes remove both.
4. **In a committed project, `cortex.toml` against the team's file.** (a) The highest wins, as written: both are the team's; (b) the project's wins, the most specific, so that a project sharing the folder may stay behind. **Recommended: (a)** — the maintainer's reasoning for 18, a higher pin validated by the team's process, holds for a committed pin too. Its cost: a project that shares the folder cannot stay behind it (§5).
