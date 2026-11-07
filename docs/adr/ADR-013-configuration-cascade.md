# ADR-013 — The configuration cascade: the team's settings, the machine's, and where each value comes from

- **Status:** Proposed
- **Date:** 2026-11-07 (proposed, with the maintainer's arbitrations of 2026-11-06 and 2026-11-07, §2)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `core/cortex_core/project.py` (the grammar of the team's file and the machine's, the new keys, the resolution), `core/cortex_core/resolver.py`, `catalog.py`, `validate.py` and `workspace.py` (the team's folder), `cli/cortex_cli/` (`config`, `sync`, `upgrade`, `init`, `validate`, `registry`, a new `cortex config` command), `runtime/cortex_runtime/base.py` (the version the runtime reads), `templates/bootstrap-instructions*.md` (the version and theme rules, the team's folder), `README.md`, `docs/getting-started.md`, `docs/extending-layers.md`
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) §3.3 (`~/.cortex/config.toml`, reserved), §3.4 (the two files, of which this record extends the grammar), §7.1 (this follow-up) and §9, phase 4 (a team-wide pin for a workspace root that is no repository) · [ADR-010](ADR-010-lock-and-upgrade.md) §3.2 to §3.5 (the lock, `cortex sync`, `cortex validate`, `cortex upgrade`) and §3.8 (the bootstrap keeps the machine in step) · [ADR-006](ADR-006-workspace-shareable-repo.md) (the team tier, `agents/`) · [ADR-012](ADR-012-bootstrap-block.md) (Cortex's block, which may name the team's folder) · [ADR-011](ADR-011-project-registry.md) §3.3 (`CORTEX_NO_REGISTER`) · roadmap epic [#98](https://github.com/xod-labs/cortex/issues/98), first of its two records · [#327](https://github.com/xod-labs/cortex/issues/327) · [#40](https://github.com/xod-labs/cortex/issues/40)

---

## 1. Context

ADR-008 left the configuration cascade to #98 (§7.1): four tiers — organisation ▸ project ▸ project-local ▸ machine — of which `cortex.toml` and `cortex.local.toml` are the two in the middle, and `~/.cortex/config.toml` reserved for the machine (§3.3). Four gaps have shown since:

- **A team that does not commit its root cannot pin its version.** At a workspace root that is no git repository — flavor 2.B of ADR-006 — `cortex.toml` is committed nowhere, and pinning is per developer (ADR-008 §9). Moving Bluspark's workspace to 1.7.0 on 2026-11-07 moved one machine; each teammate stays on their own version until they move it themselves. ADR-008 §9 named the answer and left it here: a `cortex.toml` in the team's `agents/` repository.
- **The machine's settings are environment variables only**: `CORTEX_HOME`, `CORTEX_RELEASES_URL`, `CORTEX_NO_UPDATE_CHECK`, `CORTEX_NO_REGISTER`. Nothing writes them down, and nothing says which one is set when a command behaves otherwise than expected.
- **The team's folder is `agents/`, fixed.** The resolver, the capability catalog, `cortex validate` and the service scan all read `{project_root}/agents/`. A team whose overlays live in one repository for several workspaces clones it at `agents/` in each.
- **Trying a version is moving it.** In a project whose root is a repository, a developer tries a newer version on a branch. At a 2.B root, nothing tells a developer's try from the team's choice.

On 2026-11-06 the maintainer cut #98 in two: this record, then the IDE modes, which #137 will validate.

## 2. Decision

**Cortex reads its settings from five places — the environment, the developer's `cortex.local.toml`, the project's `cortex.toml`, the team's `cortex.toml` in the team's folder, and the machine's `config.toml` — and for each key the most specific value wins. Two exceptions: for `version`, the highest wins; a key the team's file locks takes the team's value. The project names the team's folder, `agents/` by default. `cortex config` says each value and where it comes from.**

- **The team's file**, `cortex.toml` in the team's folder, gives defaults to every project that reads the folder, and imposes the keys it lists in `locked`. It is committed in the team's repository. At a 2.B root, it is the team's pin.
- **`version`: the highest wins** among the team's file, `cortex.toml` and `cortex.local.toml`. A developer may read a version above the team's to try it before proposing it. The commands say so at every sync, and say when the team has caught up.
- **The lock** sits beside the file whose `version` won: the team's lock in the team's folder, the project's at its root as in ADR-010. A developer's version never enters the team's lock, and `cortex.local.toml`'s is not locked at all.
- **The machine's file**, `~/.cortex/config.toml`, holds the machine's settings, and today's variables override it.
- **`agents`**, a key of `cortex.toml` and `cortex.local.toml`, names the team's folder: a path relative to the project root, which may lead outside it.
- **`cortex config`** prints each setting, its value and its origin.
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

Out of scope: the IDE modes, and the rendering of resolved prompts (#98, its second record); a team's repository named by git URL and pinned in the lock (#327); the endpoints of local models, which a later record puts in the machine's file (#40); the runtime's own variables — `CORTEX_DB`, `CORTEX_AUTH` and the others of ADR-002 to 005.

## 3. Detailed contract

### 3.1 The tiers

| Tier | Where | Committed | Written by |
|---|---|---|---|
| environment | the `CORTEX_*` variables of §3.7 | — | the developer's shell, a CI |
| local | `cortex.local.toml`, at the project root | never | the developer; `cortex sync` for `spec` and `claude_entry` |
| project | `cortex.toml`, at the project root | in the project's repository — nowhere at a root that is no git work tree (ADR-008 §9) | `cortex init`, `cortex upgrade`, the team |
| team | `cortex.toml` in the team's folder (§3.6) | in the team's repository | the team; `cortex upgrade --team` |
| machine | `config.toml` in the Cortex home — `$CORTEX_HOME`, `~/.cortex` by default | never | the developer |

For each key, the value is that of the first tier that sets it, from the top of the table down. Two exceptions:

- **A key the team's file locks** (§3.5) takes the team's value, whatever a tier above it says.
- **`version`** takes the highest value (§3.3).

A key no tier sets takes its default. The project root is where `cortex.toml` is, the workspace root in workspace mode, as in ADR-008 §3.4.

### 3.2 The keys, tier by tier

| Key | Environment | Local | Project | Team | Machine | Default |
|---|---|---|---|---|---|---|
| `version` | — | ✓ | ✓ | ✓ | — | none: required in `cortex.toml` or the team's file |
| `theme` | — | ✓ | ✓ | ✓ | — | none: required in `cortex.toml` or the team's file |
| `sync` | — | — | ✓ | ✓ | — | `store` |
| `claude_access` | — | ✓ | ✓ | ✓ | — | unset: Claude Code's settings left alone |
| `agents` | — | ✓ | ✓ | — | — | `agents` |
| `locked` | — | — | — | ✓ | — | `[]` |
| `releases_url` | `CORTEX_RELEASES_URL` | — | — | — | ✓ | GitHub's releases of `xod-labs/cortex` |
| `update_check` | `CORTEX_NO_UPDATE_CHECK` | — | — | — | ✓ | `true` |
| `register` | `CORTEX_NO_REGISTER` | — | — | — | ✓ | `true` |

- **A key a tier does not take is an error in that tier's file**, as an unknown key is (ADR-008 §3.4).
- **`version` and `theme` become optional in `cortex.toml` when the team's file sets them.** Required in one of the two still: a project where neither sets `version`, or `theme`, is refused, naming both.
- **`releases_url`, `update_check` and `register` are the machine's**, and the environment's. A committed file never decides where a machine downloads from, nor whether it keeps its registry: a commit to a team's repository would otherwise send every teammate's downloads to another host.
- **`spec` and `claude_entry`** stay `cortex sync`'s, in `cortex.local.toml` only (ADR-008 §3.4).
- **`CORTEX_HOME` stays a variable only**: it says where the machine's file is.

### 3.3 `version`: the highest wins

- **The candidates** are the `version` of the team's file, of `cortex.toml` and of `cortex.local.toml`. The resolved version is the highest, in Semantic Versioning's order: a pre-release sorts below its release, `1.9.0-rc.1` below `1.9.0`.
- **`version` is never lockable**: the team's file that lists it in `locked` is refused (§3.5).
- **The developer's version** is that of `cortex.local.toml`, and, at a root that is no git work tree, that of `cortex.toml`, which is committed nowhere. The team's version is the highest of the others: the team's file, and `cortex.toml` in a git work tree.
- **On a tie**, the team's version wins over the developer's, and a committed `cortex.toml` over the team's file: the file that won decides the lock (§3.4), and a project of 1.7.0 that adds a team's file at its own version keeps reading its own lock.
- **Ahead.** When the developer's version is above the team's — when there is one — `cortex sync`, `cortex upgrade` and `cortex validate` say it, every time, and, of `cortex.local.toml`'s, that it is not locked:

  ```text
  note: Cortex 1.9.0-rc.1 is read — cortex.local.toml's, above the team's 1.8.0 (agents/cortex.toml). It is not locked: its release is checked against its SHA256SUMS.
  ```

- **Caught up.** When the developer's version is at or below the team's, it changes nothing, and the same commands say so:

  ```text
  note: cortex.local.toml's version, 1.9.0-rc.1, is below the team's 1.9.0 (agents/cortex.toml) and changes nothing — remove it.
  ```

  Nothing removes it but the developer: a command never edits a key of theirs.
- **A project pin below the team's** — `cortex.toml` at `1.7.0` in a git work tree, the team's file at `1.8.0` — reads `1.8.0`, and the commands say that `cortex.toml`'s pin changes nothing.
- **Without a team's file**, and without `version` in `cortex.local.toml`, the resolved version is `cortex.toml`'s, as in 1.7.0.

### 3.4 The lock

The lock sits **beside the file whose `version` won**:

| The version comes from | The lock |
|---|---|
| `cortex.toml` | `cortex.lock` at the project root — ADR-010 §3.2, unchanged: at a root that is no git work tree, the developer's own, committed nowhere |
| the team's file | `cortex.lock` in the team's folder, committed in the team's repository, with ADR-010's grammar |
| `cortex.local.toml` | none: the release is checked against its `SHA256SUMS` when it is downloaded (ADR-008 §3.3), as every version is, and `cortex sync` says the version is not locked |

- **`cortex sync` applies ADR-010 §3.3's table to that lock**, and reads no other. Its *commit it* names where: *agents/cortex.lock written — commit it in agents/*.
- **`cortex sync --locked` resolves without `cortex.local.toml`**: a CI has none, and checks what the team committed. A developer's version never reaches it. A CI whose team's folder is another repository checks it out there first.
- **`cortex validate`** gives ADR-010 §3.4's warnings against the lock of the version that won, and compares the spec with the resolved version, not with `cortex.toml`'s.

### 3.5 The team's file

```toml
# The team's Cortex settings, for every project that reads this folder (ADR-013). Committed in the team's repository.
version = "1.8.0"
theme = "h2g2"
claude_access = true
locked = ["claude_access"]
```

- **Where:** `cortex.toml` in the team's folder (§3.6). Optional: without it, there is no team tier.
- **Its keys:** `version`, `theme`, `sync`, `claude_access` and `locked`, none required. The grammar is the core's, as for the project's files: an unknown key is an error.
- **`locked`** is an array of the keys the file sets. A key it does not set is refused, and so is `version` (§3.3).
- **A locked key that a tier above sets otherwise** takes the team's value. `cortex sync` and `cortex validate` warn, naming the file, the key and both values; `cortex validate --strict` fails.
- **It is read in every layout** that has a team's folder: at a 2.B root it is the only committed tier; in a committed project it is a second one, for teams that share a folder between several projects.
- **It is no project.** The walk up to the nearest `cortex.toml` (ADR-008 §3.5) passes a `cortex.toml` that is the team's file of a project above it: `cortex sync` and `cortex validate` run inside the team's folder work on that project. `cortex init` refuses to make the team's folder a project. A team's folder outside the project root has no project above it to say so: a command run inside it reads its file as a project's — open arbitration 1.

### 3.6 The team's folder: `agents`

```toml
agents = "../bluspark-agents"    # cortex.toml — a path relative to the project root
```

- **Where it is set:** `agents` in `cortex.toml`, a relative path — an absolute one is refused there, a path of one machine — and in `cortex.local.toml`, relative or absolute: where this machine has its clone. Not in the team's file, which is in the folder the key names; nor in the machine's file or the environment, which are not the project's.
- **Default:** `agents`, the folder of ADR-006.
- **Refused:** a value that names the project root itself, or `cortex`.
- **What follows the folder:** the workspace tier of the cascade (ADR-001 §3.1 — the resolver and the capability catalog); the team's context files (ADR-006 §3.2); the overlay roots `cortex validate` checks; the folder the service scan leaves out; the team's file and its lock; the project's own themes, which `cortex init` and `cortex sync` look for; and Cortex's block, where the templates name the project's folder — `agents/…` in a single project's, `{workspace_root}/agents/…` in a workspace's — never `cortex/agents/…`, which is the spec.
- **Cortex's block names the folder.** `cortex init` and `cortex upgrade` write the configured folder where the templates name the project's `agents/`; ADR-012 lets them, the block being Cortex's. A block written for another folder is one nobody changed — its digest says so — and `cortex upgrade` writes it again, as for a theme that changed (ADR-010 §3.5, steps 1 and 5).
- **A folder that is not there:** the default `agents/` missing is silent, as in 1.7.0. A folder the key names, missing, is a warning at every sync: its overlays, its context and its settings are not read — clone it there, or set `agents` in `cortex.local.toml`.
- **What stays:** a service's folder, `{service}/agents/`; the `Base:` headers, whose `cortex/agents/…` names the spec.

### 3.7 The machine's file and the environment

```toml
# This machine's Cortex settings, for every project of this user (ADR-013).
releases_url = "https://mirror.example.com/cortex/releases"
update_check = false
register = false
```

- **Where:** `config.toml` in the Cortex home, `~/.cortex/config.toml` by default — the file ADR-008 §3.3 reserved. Optional; no command writes it.
- **The variables override it**, as they behave in 1.7.0: `CORTEX_RELEASES_URL` gives `releases_url`; `CORTEX_NO_UPDATE_CHECK` and `CORTEX_NO_REGISTER`, set to a value that is not empty, make `update_check` and `register` false.
- **It is read outside a project too**, by `cortex self-update`. The install scripts read `CORTEX_RELEASES_URL` only: they run before any `cortex` does.
- **The environment over a locked key** — rule 13 — concerns no key in 1.8.0: the team's file locks none of the machine's keys. The rule holds for the keys a later record gives both.

### 3.8 `cortex config`

```text
cortex config [KEY] [--json]
```

```text
version        1.9.0-rc.1    cortex.local.toml — above the team's 1.8.0 (agents/cortex.toml)
theme          h2g2          agents/cortex.toml — locked
sync           store         default
claude_access  true          cortex.toml
agents         agents        default
releases_url   https://github.com/xod-labs/cortex/releases    default
update_check   true          default
register       false         CORTEX_NO_REGISTER
```

- **Each key**, its value and its origin: a file, as the project root names it, a variable, or *default*. A locked key says so; `version` names the team's version when the developer's is above it.
- **`KEY`** prints that key alone. **`--json`** prints an object: each key's value, origin, the values it overrode, and whether it is locked.
- **Outside a project**, the machine's keys only.
- **It reads, and writes nothing.** A refused file exits 1, naming the file and the key, as `cortex sync` does.

### 3.9 The bootstrap rules

The first rules of both templates (ADR-010 §3.8) read the version and the theme the way the commands resolve them. The rule on the version becomes:

| The assistant finds | It |
|---|---|
| a spec of another version than the highest `version` of `cortex.local.toml`, `cortex.toml` and the team's `cortex.toml` | runs `cortex sync`, and goes no further until it succeeded |

- **The active theme** is `theme` in `cortex.local.toml`, else in `cortex.toml`, else in the team's file — and the team's file's own when it lists `theme` in `locked`.
- **Where the team's file is:** in the folder Cortex's block names (§3.6).
- **The assistant never moves a pin of its own accord** (ADR-010 §3.8): the team's file and `cortex.local.toml`'s `version` as `cortex.toml`'s. Asked by the user, it runs `cortex upgrade`, `--team` or `--local`, the dry run first.
- **No new format.** The templates stay format 2; their text changes, and `cortex upgrade` writes it again in every block nobody changed.

### 3.10 The commands, the registry and the runtime

- **`cortex sync`** puts the resolved version in the store, checks it against the lock of §3.4, and says the notes of §3.3, §3.5 and §3.6.
- **`cortex upgrade`** moves `cortex.toml`'s `version`, as in 1.7.0. **`--team`** moves the team's file instead, and its lock; `--branch` and `--commit` then act in the repository the team's folder is in. **`--local`** writes `cortex.local.toml`'s `version` and syncs; it writes no bootstrap, which is committed. ADR-010 §3.5's rules on the target apply to the version the moved file pins — the resolved one when it pins none. When the version that file pins is not the one resolved — `cortex.toml` moved to `1.8.0` below a team's `1.9.0` — the command says which one is read.
- **`cortex init`** writes neither `version` nor `theme` in `cortex.toml` when the team's file sets them: a root `version` written from the binary would put a developer ahead of the team on their first init.
- **`cortex validate`** checks the spec against the resolved version (§3.4), and warns on a locked key overridden (§3.5) and on a missing folder (§3.6).
- **`cortex prune`** keeps, of each registered project, the version it resolves on this machine — `cortex.local.toml`'s included, which the developer reads.
- **`cortex project list`** gives as `PINNED` the version resolved without `cortex.local.toml`: what the team pins. `--json` adds its origin. `READS` is unchanged.
- **The runtime** reads the version and the theme through the core's resolution, from the committed tiers — the team's file and `cortex.toml` — and never from `cortex.local.toml`, whose paths are the host's, which its container does not see (ADR-008 §3.6). A team's folder outside the project root is mounted with it.
- **A file that pins a version newer than the binary** is refused for that, before its keys are checked: from 1.8.0 on, a key a later version adds reads as *this cortex is too old — `cortex self-update`*, which the bootstrap's rule acts on, and not as an unknown key.

### 3.11 What stays as it is

- A project with no team's file, no `version` in `cortex.local.toml` and no `agents` key resolves as in 1.7.0, and so does every variable.
- An unknown key is an error (ADR-008 §3.4); the lock's grammar, format 1 (ADR-010 §3.2); the store; the marker and format 2 (ADR-012); the services' folders; the `Base:` headers; the runtime's own variables.
- ADR-008 §3.4 and ADR-010 §3.2 to §3.4 and §3.8 record in their amendments, once this record is implemented, the keys it adds and the resolved version that replaces `cortex.toml`'s where they read it.

## 4. Phases

Execution order: **1 → 2 → 3 → 4**. All ship in one release, 1.8.0: new keys and files, every one optional, and nothing a project must change.

### Phase 1 — The resolution, the machine's file and `cortex config`

§3.1, §3.2, §3.7, §3.8, the grammar of §3.5, and the last item of §3.10.

Acceptance criteria:

- the core resolves every key of §3.2 with its origin, for a project and outside one; the CLI reads it from there
- the grammar takes the team's file, the machine's file and `version` in `cortex.local.toml`, and refuses a key a tier does not take, `version` in `locked`, and a locked key the file does not set
- `releases_url`, `update_check` and `register` read from the machine's file, the variables over it; every variable behaves as in 1.7.0
- `cortex config`, `cortex config KEY` and `--json` give each value and its origin; a refused file exits 1
- a file pinning a version newer than the binary is refused for that before its keys are checked
- a project of 1.7.0 resolves to what 1.7.0 reads, key by key

### Phase 2 — The team's file: the version, the lock and the defaults

§3.3, §3.4, §3.5, the version and theme rules of §3.9, and §3.10.

Acceptance criteria:

- the highest version wins among the three files, a pre-release below its release; the notes of §3.3 are said at every sync, upgrade and validate
- the lock is written and checked beside the file whose version won; a developer's version writes none; `--locked` resolves without `cortex.local.toml`
- `cortex validate` compares the spec with the resolved version, and warns on a locked key overridden
- `cortex upgrade --team` moves the team's file and its lock, `--branch` and `--commit` in the team's repository; `--local` writes `cortex.local.toml` and no bootstrap
- `cortex init` writes no `version` nor `theme` the team's file sets
- the walk up passes the team's file; `cortex init` refuses the team's folder
- `cortex prune` keeps each project's resolved version, `cortex.local.toml`'s included; `cortex project list` gives the team's pin as `PINNED`; the runtime reads the committed tiers
- both templates give the version and theme rules of §3.9; the init matrix differs by those lines only

### Phase 3 — The team's folder

§3.6.

Acceptance criteria:

- the grammar takes `agents` in `cortex.toml`, relative, and in `cortex.local.toml`; `cortex config` shows it
- `agents` in `cortex.toml` or `cortex.local.toml` moves the workspace tier, the team's context files, the overlay roots of `cortex validate`, the service scan's exclusion, the team's file and its lock, and the project's own themes
- a path leading outside the project root works; a value naming the root or `cortex` is refused
- `cortex init` and `cortex upgrade` write the configured folder in Cortex's block; `cortex upgrade` writes again a block nobody changed that names another folder
- a folder the key names, missing, is warned at every sync; the default missing is silent

### Phase 4 — The fixes that ride along

Older than this record, each found on the way to 1.8.0:

- **#328** — `cortex init` says a link Cortex made is not Cortex's once `cortex.toml` is gone.
- **#329** — `cortex init --force` without `--workspace` writes a single project's bootstrap over a workspace's; with it, the three mutants Bluspark's second counter-review of 1.7.0 left (WS2 to WS4, its comment on #329), and the line of amendment ADR-001 §3.5 owes ADR-012 §9.
- **`cortex upgrade`'s ending** says *Commit cortex.toml, cortex.lock* at a root that is no git work tree, where `cortex init` says they are committed nowhere.
- **The workspace template's step 4.2** lists the `@alias` values from the services' `project-overview.md`, while 1.7.0 declares them in a table below the end marker.

## 5. Consequences

### Positive
- A team pins its version at a 2.B root: one commit in its repository moves every teammate, whose assistant syncs after the pull (ADR-010 §3.8).
- A developer tries a version before the team, without a branch and without touching a committed file, and is told at every sync.
- The machine's settings have a file, and `cortex config` says why a command reads what it reads.
- A team's folder can serve several workspaces, cloned once beside them.

### Negative
- **The assistant reads one more file**, the team's, when there is a team's folder, to compare versions and find the theme (§3.9) — the kind of reading ADR-012 §6 declined to add for a team's rules, and which #137 has not measured outside Claude Code. The IDE modes, which render the resolved values, remove it.
- **A cortex before 1.8.0 refuses a `cortex.toml` that uses a key of 1.8.0** — `agents`, or no `version` — as an unknown or missing key, not as a binary too old: its assistant is not sent to `cortex self-update`. A team sets the new keys once every teammate runs 1.8.0. From 1.8.0 on, a later key is refused as a version too new (§3.10).
- **A cortex before 1.8.0 does not read the team's file.** A teammate who has not updated keeps reading the version of their own `cortex.toml`, unwarned, until they run `cortex self-update` — which moving the team's pin calls for, said by the team.
- **Two locks may exist** in a committed project whose team's file pins above `cortex.toml`: only the lock of the version that won is read, and the other goes stale until `cortex upgrade` brings `cortex.toml` level.
- **A developer ahead stays ahead** until they remove their version or the team catches up. The notes of §3.3 say it at every sync.

### Neutral
- `cortex.toml` and `cortex.local.toml` keep their names, their place and their meaning; the team's file has the project's name, in another folder.
- The variables keep their names and their effect; the machine's file gives them a default.

## 6. Alternatives considered

- **The developer's pin over the team's**, the most specific winning for `version` too — ADR-008 §9's wording. A developer who has not moved would hold the version the team validated back on their machine. Rejected by the maintainer (18).
- **A lockable `version`.** It would forbid trying a version before proposing it. Rejected by the maintainer (19).
- **The lock at the root of a 2.B workspace.** Committed nowhere, it guarantees the team nothing. Rejected by the maintainer (20).
- **The lock inside the team's file.** It would mix what the team writes and what `cortex sync` writes, which ADR-010 §3.2 keeps apart. Rejected.
- **A variable for every key**, `CORTEX_THEME`, `CORTEX_VERSION`. A committed setting would then change with a shell, unseen by the team. Rejected: the environment keeps the machine's keys.
- **`releases_url` in the committed tiers.** A commit to the team's repository would redirect every teammate's downloads. Rejected: the machine's and the environment's only.
- **A team's repository named by git URL**, fetched by `cortex sync` and pinned in the lock. Its own record, #327, later.

## 7. Follow-ups (out of scope for this ADR)

1. **The IDE modes** (#98, its second record) — the resolved prompts rendered into Cortex's block, which removes the cascade and the comparisons the templates ask of the assistant (§3.9).
2. **A team's repository shared by git URL** (#327) — named in `cortex.toml`, fetched into the store, its commit in the lock.
3. **The endpoints of local models** (#40), in the machine's file.
4. **`cortex config set`**, should editing the files by hand prove error-prone.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.3 the store and `config.toml`, §3.4 the project's files, §7.1 this follow-up, §9 phase 4 the 2.B pin
- [ADR-010](ADR-010-lock-and-upgrade.md) — §3.2 to §3.5 the lock, `sync`, `validate` and `upgrade`, §3.8 the bootstrap's rules
- [ADR-006](ADR-006-workspace-shareable-repo.md) — the team tier, `agents/`
- [ADR-012](ADR-012-bootstrap-block.md) — Cortex's block
- [ADR-011](ADR-011-project-registry.md) — §3.3 the registry and `CORTEX_NO_REGISTER`
- Issues: [#98](https://github.com/xod-labs/cortex/issues/98), [#327](https://github.com/xod-labs/cortex/issues/327), [#137](https://github.com/xod-labs/cortex/issues/137), [#40](https://github.com/xod-labs/cortex/issues/40), [#328](https://github.com/xod-labs/cortex/issues/328), [#329](https://github.com/xod-labs/cortex/issues/329)

## 9. Amendments

None yet.

## Open arbitrations

For the maintainer, before acceptance; each answer is folded into the text above, and this section removed.

1. **The team's file's name.** (a) `cortex.toml`, as arbitration 15 says it, with the walk-up rule of §3.5; (b) a name of its own, `cortex.team.toml` beside `cortex.team.lock`. Under (a), a command run inside a team's folder that lies outside the project root — the case arbitration 17 opens, `../team-agents` — finds no project above it, and reads the team's file as a project's: `cortex sync` there would write a `cortex.local.toml` into the team's repository, and a cortex before 1.8.0 may do so inside `agents/` too, when the file holds a `version` and a `theme` and no `locked`. Under (b), no reader takes the file for a project, and a cortex before 1.8.0 ignores it. **Recommended: (b)** — it departs from the name arbitration 15 gave, not from its decision: the team pins in its folder, and its lock sits beside the pin.
2. **What `cortex upgrade` moves by default once a team's file exists.** (a) `cortex.toml`, as in 1.7.0, with `--team` and `--local` (§3.10); (b) the team's file whenever it pins `version`. **Recommended: (a)** — a default that does not change with the project's files, no change of behaviour within the minor version, and at a 2.B root the default is then the developer's try, `--team` the proposal to the team.
3. **The bootstrap and the team's file.** (a) The templates read the team's file to compare versions and find the theme (§3.9); (b) `cortex sync` writes the resolved values in `cortex.local.toml`, and the templates read only that — but a team's move is then seen at the next sync, not at the next conversation. **Recommended: (a)** — the rule exists to catch a teammate's move (ADR-010 §3.8), and the IDE modes remove both.
