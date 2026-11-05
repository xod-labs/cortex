# ADR-011 — The project registry: a machine's Cortex projects, for the CLI and the cockpit

- **Status:** Accepted
- **Date:** 2026-11-04 (proposed) · 2026-11-04 (accepted, its ten arbitrations as written)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `core/cortex_core/project.py` (the grammar of `projects.toml`), `cli/cortex_cli/` (a new `cortex project` command, `init`, `sync`, `prune`, `upgrade`), `README.md`, `docs/getting-started.md`, `docs/extending-layers.md`
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) §3.3, whose store "keeps no list of projects", which this record revisits for the machine, not for the store · [ADR-010](ADR-010-lock-and-upgrade.md) §3.6 and §7.4, `cortex prune DIR...`, which takes no `DIR` once this registry exists · roadmap epic [#266](https://github.com/xod-labs/cortex/issues/266), split from [#48](https://github.com/xod-labs/cortex/issues/48) on 2026-11-04 · [#98](https://github.com/xod-labs/cortex/issues/98), the configuration cascade, which keeps `~/.cortex/config.toml`

---

## 1. Context

A machine holds several Cortex projects, and nothing lists them. ADR-008 decided so for the store: a version directory is shared, and nothing in it is per project (§3.3). ADR-010 then needed the list twice, and asked the user for it:

- **`cortex prune DIR...`** keeps the versions of the projects under the directories it is given. Without a `DIR` it exits 2, since pruning blind would remove the versions of every project it was not shown.
- **Moving every project to a new release** is one `cortex upgrade` per project, run from each project's root.

A third reader is coming: the cockpit (#46) must find a machine's projects to show them, and the multi-project runtime (#48) was planned on a registry. On 2026-11-04 the maintainer split the registry out of #48. The CLI's half is this record, and the runtime's half stays #48's. The maintainer's answers are recorded in #266:

- a project is added **both ways**, by hand and on its own;
- `cortex prune` **drops** the projects no longer on disk, and a check may report them without dropping anything;
- there is **one entry per workspace**, because how a team splits its repositories is the team's choice, not Cortex's;
- `cortex upgrade --all` may create a branch or a commit, **as an option, never by default**, and says what each project must commit;
- the file is **`~/.cortex/projects.toml`**.

## 2. Decision

**A machine keeps the list of its Cortex projects in `projects.toml`, in the Cortex home. `cortex init` and `cortex sync` add a project to it, and `cortex project` manages it by hand. `cortex prune` with no `DIR`, and `cortex upgrade --all`, read it.**

- **The file** holds one entry per project root — the directory of `cortex.toml`, the workspace root in workspace mode — with a name and a path. Nothing else: what a project pins, and what it reads now, are read from the project each time.
- **`cortex project add | list | remove | check`** manages the file. `list --json` is what the cockpit reads.
- **`cortex init` and `cortex sync` register the project** they work on when it is not listed, and say so once. `CORTEX_NO_REGISTER` turns this off.
- **`cortex prune` with no `DIR`** keeps the versions of the registered projects, and drops the entries whose project is gone. `cortex prune DIR...` works as in 1.4.0.
- **`cortex upgrade --all`** moves every registered project. It goes on past a project that refuses, and ends with what each one must commit. `--branch` and `--commit` are options.

Out of scope: the multi-project runtime and what it reads of the registry (#48); creating a project from a repository, which the maintainer mentioned for the cockpit (§7); the configuration cascade (#98).

## 3. Detailed contract

### 3.1 `projects.toml`

```toml
# The Cortex projects of this machine (ADR-011). Written by cortex — `cortex project` edits it.
format = 1

[[project]]
name = "acme"
path = "/home/dev/code/acme"

[[project]]
name = "xod-labs"
path = "/home/dev/Documents/Work/Personal/xod-labs"
```

- **Where:** `$CORTEX_HOME/projects.toml`, by default `~/.cortex/projects.toml`, beside the store. The file is not `config.toml`, which ADR-008 keeps for the machine tier of the configuration cascade (#98).
- **`format`** is the file's own format, 1 here. A later format is refused as a later `cortex.lock` is: this cortex is older, and `cortex self-update` updates it.
- **Each `[[project]]`** has exactly two keys:
  - `name` matches `[a-z0-9][a-z0-9._-]{0,63}`, and is unique;
  - `path` is absolute, the project's root, written as the shell names it — a link is not resolved — with no C0 control character nor DEL, and unique: the grammar refuses two entries that write the same directory once normalised (`.`, `..`, a final separator, and the case on Windows), and `cortex project add` compares paths as written, then resolved, so that it never lists a directory twice — through a link, or a case the file system ignores, as on macOS.
- **The grammar is the core's** (`cortex_core.project`), as for the three files of a project: every key is required, and an unknown key is an error. The cockpit and the runtime read the file through it.
- **Nothing is cached.** A project's version, layout and theme are read from the project when needed. A second copy would disagree with it after the first `git pull`.
- **Writes are whole or not at all**: a temporary file beside it, then a rename. They are taken one at a time, under a lock on a file beside it, so two syncs that register at once both land.

### 3.2 `cortex project`

```text
cortex project add [PATH] [--name NAME]
cortex project list [--json]
cortex project remove NAME|PATH
cortex project check
```

- **`add`** registers the project at `PATH`, by default the one of the current directory — the nearest `cortex.toml`, as `cortex sync` finds it.
  - A directory with no `cortex.toml` is refused: `cortex init` makes one.
  - The name is `--name`, or the root's directory name as the shell names it, lowered, with any other character turned to `-`, the `.`, `_` and `-` it starts with dropped, cut to 64 characters, `project` when nothing is left — and `-2`, `-3`… added when the name is taken.
  - A path already listed is said, with its name, and nothing changes. `--name` with a name another path holds is refused.
- **`list`** gives, for each entry, in name order: its name, its path, the version it pins, the version this machine reads now (the `spec` of its `cortex.local.toml`), and its state:
  - `ok`;
  - `missing` — no directory there;
  - `no-project` — a directory without `cortex.toml`;
  - `unreadable` — its directory or its files cannot be read, or this cortex refuses them, with why; its versions are given when they can still be read, as `cortex prune` reads them.

  **`--json`** prints the same as one JSON object, `{"format": 1, "projects": [...]}`: an array of objects — `name`, `path`, `state`, `pinned`, `reads`, `layout` and `why`, what cannot be read of an `unreadable` one — with `null` for what is not known. `layout` is read from the marker of the project's instructions file (ADR-010 §3.1), `null` without one or when two disagree. It is the cockpit's entry point (#46). `format` is the output's own, not the file's: the keys only grow within one, and a reader refuses a later one.
- **`remove`** drops an entry, by name or by path. It never touches the project's files.
- **`check`** says each entry that is not `ok`, and exits 1 if there is one. It removes nothing, and names `cortex project remove` and `cortex prune`, which do.
- **No registry** is an empty list: `list` says so, and `check` exits 0.
- **Exit codes**: 2 for a malformed `--name`, or a `PATH` that is no directory; 1 for the other refusals — no `cortex.toml`, a name taken, nothing registered under that key, a registry refused or that cannot be written.

### 3.3 `cortex init` and `cortex sync` register the project

- **After it succeeded**, `cortex init` or `cortex sync` adds the project's root when the registry does not list it, named as `add` names it, and says so once: *registered as acme — `cortex project list` lists this machine's projects*.
- **It never renames or moves an entry.** A project moved on disk is registered again at its new path, under a free name. Its old entry is then `missing`, and the next `cortex prune` or `cortex project remove` drops it.
- **A registry that cannot be written is a warning**, never a failed sync: the project is synced, and it is not listed.
- **`CORTEX_NO_REGISTER`**, set to anything but an empty value, keeps both commands from writing the registry. It is for a CI runner, or a throwaway project, though an ephemeral runner's registry goes with it.
- **`cortex sync --from PATH` registers too.** The project is a project: the source of its spec does not matter here.

### 3.4 `cortex prune` with no `DIR`

```text
cortex prune [DIR...] [--keep VERSION]... [--dry-run]
```

- **With no `DIR`**, the projects are the registry's. The versions kept are the same as with directories (ADR-010 §3.6): what each project pins and reads now, the binary's own version, and each `--keep`.
- **An entry whose project is gone** — `missing`, or `no project` — is dropped from the registry, and said: *acme: /home/dev/code/acme is gone — dropped from the registry*. Its version is no longer kept for it. A project on a drive that is not mounted is gone too: its next sync registers it again, and gets its version back, checked against its lock.
- **An `unreadable` entry is kept**, and named. Its versions are kept when they can be read, as under a `DIR`, and not known otherwise (ADR-010 §3.6).
- **An empty registry, and no `DIR`,** exits 2, as before: there is nothing to keep, and pruning would remove every version.
- **With `DIR...`**, nothing changes from 1.4.0: the projects are those under the directories, and the registry is neither read nor written.
- **`--dry-run`** lists, removes nothing, and drops no entry.

### 3.5 `cortex upgrade --all`

```text
cortex upgrade --all [VERSION] [--dry-run] [--force] [--branch NAME] [--commit --message MESSAGE]
```

- **Each registered project, in name order**, gets the upgrade of ADR-010 §3.5 to the same target: `VERSION`, or the binary's own version. The projects are not moved down unless `VERSION` names the version.
  - A project that is not `ok` is skipped, and said.
  - The release check of ADR-010 §3.5 asks once, not once per project.
- **A project that refuses does not stop the others** — overlays that fail, a sync that refuses, a pin newer than the binary. The command ends with one line per project — *moved 1.4.0 → 1.5.0*, *already on 1.5.0*, *refused: …*, *skipped: missing* — and exits 1 if any project refused.
- **It ends with what each project must commit**, by path: `cortex.toml`, `cortex.lock`, the bootstrap files written. **Nothing is committed** unless `--commit` is given, and the command says so.
- **`--branch NAME`** creates and switches to the branch `NAME` in each project that is a git work tree, before writing anything. A project where `NAME` already exists is refused, and the others go on. Nothing is stashed: changes the team has not committed stay where they are.
- **`--commit`** commits, in each project that moved, the files the upgrade wrote and nothing else, with `--message`, which is required: a team's commit convention is its own. Nothing is pushed. A project that is not a git work tree, or whose files git ignores — a workspace whose `cortex.toml` is not committed — is said, and nothing is committed there.
- **`--dry-run`** is the dry run of each project, and creates no branch. `--force` is passed to each project.
- **Without `--all`**, `--branch` and `--commit` work the same for the one project.
- **The assistant never runs it of its own accord**, as it never runs `cortex upgrade` (ADR-010 §3.8). It runs it when the user asks, the dry run first.

### 3.6 What reads the registry besides the CLI

- **The cockpit (#46)** reads `cortex project list --json`, or the file through the core's grammar. It does not write it in this record: the commands do.
- **The multi-project runtime (#48)** is its own record. It may read the same file, and may extend the format with one of its own.

### 3.7 What stays as it is

- The store's layout: nothing in it is per project. The registry sits beside it, not in it.
- `cortex.toml`, `cortex.local.toml`, `cortex.lock`: no new key. A project does not know it is registered.
- `cortex prune DIR...` and `cortex upgrade` without `--all`, as in 1.4.0.

## 4. Phases

Execution order: **1 → 2 → 3 → 4**. All four ship in one release, 1.5.0: new commands, and a file on the machine; nothing a project must change. The ADR-010 text corrections of #279 ride along.

### Phase 1 — The registry and `cortex project`

The file of §3.1, its grammar in the core, and the command of §3.2.

Acceptance criteria:

- `cortex project add` in a project registers its root, named after its directory, and says so; run again, it says the path is listed and changes nothing
- `--name` names it; a taken name is refused; a directory with no `cortex.toml` is refused
- `list` gives each entry's name, path, pinned version, version read now and state; `--json` gives the same as one object, its `format` and its `projects`, `null` for what is not known
- `remove` drops an entry by name and by path, and leaves the project's files byte for byte
- `check` exits 1 on a `missing`, a `no-project` and an `unreadable` entry, and removes nothing; exits 0 on an empty or missing registry
- a registry with an unknown key, a name that does not match, two entries written the same once normalised, a path with a C0 control character or DEL, or `format = 2` is refused, naming the file and what is wrong
- two `add` run at once both land

### Phase 2 — `cortex init` and `cortex sync` register the project

§3.3.

Acceptance criteria:

- a project synced or initialised for the first time is registered, and said once; synced again, nothing is said or written
- a project moved on disk is registered again under a free name, and its old entry is `missing`
- a registry that cannot be written gives a warning, and the sync succeeds
- with `CORTEX_NO_REGISTER` set, nothing is written; empty, it counts as unset

### Phase 3 — `cortex prune` with no `DIR`

§3.4.

Acceptance criteria:

- with registered projects pinned to A and B, and a store holding A, B, C and the binary's own version, `cortex prune` removes C only
- an entry whose directory is gone is dropped and said, and its version is no longer kept for it; `--dry-run` drops nothing
- an `unreadable` entry is named and kept
- an empty registry and no `DIR` exits 2; `cortex prune DIR` neither reads nor writes the registry

### Phase 4 — `cortex upgrade --all`

§3.5.

Acceptance criteria:

- three registered projects, one with overlays the target breaks: two move, the third is refused, the others still move, and the command exits 1 with a line per project and what each must commit
- a `missing` project is skipped and said; the release check asks once
- `--branch NAME` creates the branch in each git project before writing, and refuses a project where it exists
- `--commit --message M` commits only the files the upgrade wrote, in each project that moved; `--commit` without `--message` exits 2; a project outside git is said, and nothing is committed there
- `--dry-run` changes nothing in any project and creates no branch

## 5. Consequences

### Positive

- **Maintenance runs from anywhere**: `cortex prune` and `cortex upgrade --all` need no list of directories, and `cortex project list` says where every project stands.
- **The cockpit has its entry point**: one documented JSON, through the same grammar as the CLI.
- **The registry fills itself**: a developer who syncs is registered, with nothing to remember.

### Negative

- **A file outside every project** must be kept right. A project moved or deleted leaves an entry until `prune` or `remove`.
- **Every first sync of a throwaway project registers it.** `prune` drops it once it is gone, and `CORTEX_NO_REGISTER` avoids it.
- **`cortex prune` with no `DIR` trusts the registry**: a project that was never synced on this machine since 1.5.0 is not in it, and loses its version until its next sync, as under ADR-010 with a `DIR` that did not hold it.

### Neutral

- The store is still shared and per version; nothing in it is per project.
- Projects of 1.4 and earlier are registered by the first `cortex sync` of a 1.5 binary, whatever version they pin.

## 6. Alternatives considered

- **The list in `~/.cortex/config.toml`.** Rejected: ADR-008 keeps that file for the machine tier of the configuration cascade (#98), and a list the commands write does not belong in a file a user edits.
- **The versions each project uses, cached in the registry.** Rejected: a second source that disagrees with the project after every pull. The project is read each time.
- **Registration by hand only.** Rejected by the maintainer: a list nobody keeps up is a list nobody trusts. Both ways.
- **One entry per repository, or per service of a workspace.** Rejected by the maintainer: how a team splits its repositories is its choice. One entry per root of `cortex.toml`.
- **`cortex upgrade --all` committing, or branching, by default.** Rejected by the maintainer: options, never the default, and the command says what to commit.
- **A default commit message.** Rejected: a team's commit convention is its own, so `--commit` requires `--message`.
- **`cortex project add NAME PATH`**, the form the maintainer sketched. Changed to `add [PATH] [--name NAME]`, so that `cortex project add` alone registers the current project. The name defaults to the directory's.

## 7. Follow-ups (out of scope for this ADR)

1. **The multi-project runtime** (#48) — N projects in one deployment, on this registry or a format of its own.
2. **Creating a project from a repository**, from the cockpit or `cortex project init URL` — a clone, then `cortex init`, then the registry. The maintainer mentioned it for the cockpit (#46).
3. **Several projects in one workspace** — the maintainer's example, a `xod-labs` workspace holding cortex and other products — to be defined. Today it is one entry, the workspace.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.3 the store, §3.4 the project's files, §3.5 `cortex sync`
- [ADR-010](ADR-010-lock-and-upgrade.md) — §3.5 `cortex upgrade`, §3.6 `cortex prune`, §3.8 the assistant and the pin, §7.4 this registry
- Issues: [#266](https://github.com/xod-labs/cortex/issues/266), [#48](https://github.com/xod-labs/cortex/issues/48), [#46](https://github.com/xod-labs/cortex/issues/46), [#98](https://github.com/xod-labs/cortex/issues/98)

## 9. Amendments

The record was *Accepted*, not yet implemented, when these were made: the body above already says each.

### While phase 1 was implemented (2026-11-04)

- **Two spellings of one directory** (§3.1): the grammar, which every reader shares, compares paths as text, normalised; `cortex project add` compares them resolved. A link appearing never makes the file unreadable, nor locks out `cortex project remove`.
- **The default name** (§3.2) follows the shell's spelling of the root — a link's name, not its target's — and is cut and cleaned to a valid name.
- **`unreadable` is wider** (§3.2, §3.4): a directory that cannot be searched is `unreadable`, never `missing`, so `prune` does not drop it; files this cortex refuses are `unreadable` too. Their versions are given, and kept, when they can be read loosely.
- **`--json` has a `why`**, and **`layout`** comes from the bootstrap marker, since `cortex.toml` does not record it (§3.2).
- **The exit codes** of `cortex project` are written down (§3.2).

After the counter-review of 2026-11-05:

- **`--json` is one object**, `{"format": 1, "projects": [...]}`, decided by the maintainer (§3.2): an array alone did not show which contract a reader reads, and 1.5.0 freezes it. Its `format` is the output's own.
- **`no project` is written `no-project`**, decided by the maintainer (§3.2): one word, as a value the cockpit and scripts compare.
- **Case is folded on Windows only** (§3.1): there, by the grammar; elsewhere, a case the file system ignores is told by the file system, which `cortex project add` and the registering sync ask when the paths resolved differ.
- **A path holds no C0 control character nor DEL** (§3.1). NUL, which no system takes in a file name, made every reader fail in a way of its own, and an escape in a path rewrote the terminal `list` prints to; the grammar now refuses the file, naming it. The C1 controls and the bidirectional marks pass: a file name may hold them, and a directory would then be refused a registry. A file refused is refused by every command that reads it — `cortex project remove` too — and is fixed by hand, as the guide says; only a hand's edit writes such a path, since `add` and the registering sync refuse it.
- **Paths are compared as written first** (§3.1): a project listed already is found without resolving the other entries' paths, so a share that does not answer no longer holds a sync of a listed project. A project not listed yet still has every path resolved, to find it under another spelling: registering it waits for that share.
- **A `$PWD` that is not written plainly** — a `..`, a `.`, a final `/` — is ignored, as `pwd -L` ignores it, so a path is never written so.
- **A write killed before its rename** left its temporary file: the next write sweeps it, under the lock. On Windows, a rename refused while a reader has the file open is tried again, two seconds at most.
