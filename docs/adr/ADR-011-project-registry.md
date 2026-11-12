# ADR-011 — The project registry: a machine's Cortex projects, for the CLI and the cockpit

- **Status:** Implemented
- **Date:** 2026-11-04 (proposed) · 2026-11-04 (accepted, its ten arbitrations as written, with the amendments listed in §9) · 2026-11-05 (implemented, 1.5.0)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `core/cortex_core/project.py` (the grammar of `projects.toml`), `cli/cortex_cli/` (a new `cortex project` command, `init`, `sync`, `prune`, `upgrade`), `templates/bootstrap-instructions*.md` (the rule for a spec that is gone), `README.md`, `docs/getting-started.md`, `docs/extending-layers.md`
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
- **A registry that cannot be read or written is a warning**, never a failed sync: the project is synced, and it is not listed.
- **`CORTEX_NO_REGISTER`**, set to anything but an empty value, keeps the commands that register — `cortex init`, `cortex sync` and `cortex upgrade`, which syncs — from reading or writing the registry. `cortex project` still reads and writes it: managing it is its work. So does `cortex upgrade --all` read it, the list it moves (§3.5). It is for a CI runner, or a throwaway project, though an ephemeral runner's registry goes with it. `cortex sync --locked` registers as any sync does: the option is about the lock, and on a runner this variable keeps it from registering.
- **`cortex sync --from PATH` registers too.** The project is a project: the source of its spec does not matter here. So does `cortex upgrade`, which syncs.
- **`cortex init` registers after all of its work**, not inside the sync it runs: an init that fails after its sync registers nothing.
- **A project already listed costs one read**, without the registry's lock: nothing is written, and nothing waits.

### 3.4 `cortex prune` with no `DIR`

```text
cortex prune [DIR...] [--keep VERSION]... [--dry-run]
```

- **With no `DIR`**, the projects are the registry's. The versions kept are the same as with directories (ADR-010 §3.6): what each project pins and reads now, the binary's own version, and each `--keep`.
- **An entry whose project is gone** — `missing`, or `no-project` with nothing of Cortex left — is dropped from the registry, and said: *acme: /home/dev/code/acme is gone — dropped from the registry*. Its version is no longer kept for it. A project on a drive that is not mounted is gone too: its next sync registers it again, and gets its version back, checked against its lock.
- **A `no-project` entry whose `cortex.local.toml` still reads a version stays**, and keeps that version, said in a note: a branch without Cortex checked out, an uncommitted `cortex.toml` stashed, a rebase under way. The project comes back with its pin, and its next sync, offline too, finds the version it reads. `cortex project remove` drops an entry whose project really left Cortex.
- **An `unreadable` entry is kept**, and named. The versions that can be read of it are kept, as for a project under a `DIR` whose files hold a key this binary does not know (ADR-010 §3.6) — none when not even its pin can be read.
- **An empty registry, and no `DIR`,** exits 2, as before: there is nothing to keep, and pruning would remove every version. A registry whose every project is gone is not empty: its entries are dropped, and every version but the binary's own and each `--keep` is removed.
- **The entries gone are dropped first**, in one write: a registry that cannot be written stops the command, and no version is removed. A removal that fails afterwards leaves the entries dropped, and the version in the store for the next `prune`.
- **The registry is read again just before the removals**: a project registered meanwhile, by a sync that ended while `prune` read the others, keeps its versions.
- **`CORTEX_NO_REGISTER` does not change it** (§3.3): `prune` with no `DIR` reads the registry, and drops from it, whatever its value.
- **With `DIR...`**, nothing changes from 1.4.0: the projects are those under the directories, and the registry is neither read nor written.
- **`--dry-run`** lists, removes nothing, and drops no entry.

### 3.5 `cortex upgrade --all`

```text
cortex upgrade --all [VERSION] [--dry-run] [--force] [--branch NAME] [--commit --message MESSAGE]
```

- **Each registered project, in name order**, gets the upgrade of ADR-010 §3.5 to the same target: `VERSION`, or the binary's own version. The projects are not moved down unless `VERSION` names the version.
  - A project that is not `ok` is skipped, and said.
  - The release check of ADR-010 §3.5 asks once, not once per project.
  - A `VERSION` this cortex does not serve — newer than it, or before 1.0.0 — is refused once, before any project.
  - An empty registry moves nothing: the command says so, and exits 0.
- **A project that refuses does not stop the others** — overlays that fail, a sync that refuses, a pin newer than the binary. The command ends with one line per project — *moved 1.4.0 → 1.5.0*, *already on 1.5.0*, *refused: …*, *skipped: missing* — and exits 1 if any project refused; one skipped does not. Interrupted, it ends so all the same: the project cut short, and those not reached, are said, and it exits 130.
- **It ends with what each project must commit**, by path: `cortex.toml`, `cortex.lock`, the bootstrap files written. **Nothing is committed** unless `--commit` is given, and the command says so.
- **`--branch NAME`** creates and switches to the branch `NAME` in each project that is a git work tree, once the project's checks pass and before its first write — a project with nothing to write gets none, nor does one where git ignores every file the upgrade writes. A project where `NAME` already exists is refused, and the others go on — unless `NAME` is the branch it is on, where it is written: the same command run again refuses none it moved. A branch the run made serves the other projects of its work tree; a linked work tree of the same repository is refused, since git checks a branch out in one work tree at a time. A project refused or interrupted after it keeps the branch, and says so. A project outside git is upgraded without one, and said. Nothing is stashed: changes the team has not committed stay where they are.
- **`--commit`** commits, in each project the upgrade wrote files in — moved, or finished on its pinned version — those files and nothing else, with `--message`, which is required: a team's commit convention is its own. Nothing is pushed. A project outside git, one whose `cortex.toml` git does not track — the pin is the developer's own (ADR-010 §3.8) — one where git ignores a file the upgrade wrote, and one where `cortex.toml`, or a bootstrap file the upgrade wrote, held the developer's own changes before it — they are no part of the team's commit — are said, and nothing is committed there. The pin is edited in place, which keeps a change; a bootstrap is written again only where no one changed it, so that a change — a template restored over a team's edit, say — decides it. The lock does not count: written whole from the pin, it is the same whatever it held. A file it wrote outside the project's work tree — a bootstrap linked to a shared file — is left out of the commit, and said. A project where nothing was written, but whose `cortex.toml`, lock or bootstrap files git sees changed — a run cut short, a commit refused — names them. A commit git refuses, a hook say, is that project's refusal, its upgrade done, the files left staged. A project that is a submodule says that its superproject now shows it changed.
- **`--dry-run`** is the dry run of each project, and creates no branch; it refuses a branch that exists, and a linked work tree of a repository it would make the branch in, as the run would. `--force` is passed to each project.
- **Refused before anything happens**, exit 2: `--instructions-file` with `--all`, since it names one project's file; `--commit` without `--message`, or `--message` without `--commit`; a `--branch` name git refuses, or would expand — `@{-1}`. Without git on `PATH`, `--branch` and `--commit` are refused, exit 1, nothing changed.
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
- a registry that cannot be read or written gives a warning, and the sync succeeds
- with `CORTEX_NO_REGISTER` set, the registry is neither read nor written; empty, it counts as unset

### Phase 3 — `cortex prune` with no `DIR`

§3.4.

Acceptance criteria:

- with registered projects pinned to A and B, and a store holding A, B, C and the binary's own version, `cortex prune` removes C only
- an entry whose directory is gone is dropped and said, and its version is no longer kept for it; `--dry-run` drops nothing
- a `no-project` entry whose `cortex.local.toml` still reads a version stays, and keeps it
- an `unreadable` entry is named and kept
- an empty registry and no `DIR` exits 2; `cortex prune DIR` neither reads nor writes the registry

### Phase 4 — `cortex upgrade --all`

§3.5.

Acceptance criteria:

- three registered projects, one with overlays the target breaks: two move, the third is refused, the others still move, and the command exits 1 with a line per project and what each must commit
- a `missing` project is skipped and said; the release check asks once
- `--branch NAME` creates the branch in each git project before writing, and refuses a project where it exists unless it is checked out there; the same command run again refuses none it moved
- `--commit --message M` commits only the files the upgrade wrote, in each project that moved or finished on its pinned version, and nothing where `cortex.toml`, or a bootstrap file it wrote, held the developer's own changes — a lock never committed is committed; `--commit` without `--message` exits 2; a project outside git is said, and nothing is committed there
- `--dry-run` changes nothing in any project and creates no branch

## 5. Consequences

### Positive

- **Maintenance runs from anywhere**: `cortex prune` and `cortex upgrade --all` need no list of directories, and `cortex project list` says where every project stands.
- **The cockpit has its entry point**: one documented JSON, through the same grammar as the CLI.
- **The registry fills itself**: a developer who syncs is registered, with nothing to remember.

### Negative

- **A file outside every project** must be kept right. A project moved or deleted leaves an entry until `prune` or `remove`.
- **Every first sync of a throwaway project registers it.** `prune` drops it once it is gone, and `CORTEX_NO_REGISTER` avoids it.
- **`cortex prune` with no `DIR` trusts the registry**: a project that was never synced on this machine since 1.5.0 is not in it, and loses its version until its next sync, as under ADR-010 with a `DIR` that did not hold it. Its assistant does not sync a project already on its version, so such a project stays unregistered until someone syncs it, or adds it, by hand: the release note says to, before the first `prune` with no `DIR`. The instructions file has a rule for what is left: *the directory `spec` names does not exist* → run `cortex sync`, which gets the version back — offline, it says what it cannot download.
- **A sync and a `prune` at the same moment**: a project registered after `prune` read the registry the second time, just before the removals, may lose a version it synced from the store meanwhile. The same rule of the instructions file has its next conversation sync it back.

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

### While phase 2 was implemented (2026-11-04)

- **`cortex upgrade` registers too** (§3.3), since it syncs; and `cortex init` registers after all of its work, not inside its sync.
- **A registry that cannot be read**, not only one that cannot be written, is a warning (§3.3): a later format, or a file the grammar refuses, never fails a sync.
- **`CORTEX_NO_REGISTER` stops reads too** (§3.3), so that a broken registry draws no warning where registration is off.

After the counter-review of 2026-11-05:

- **§3.3 names the three commands** that register, and what `CORTEX_NO_REGISTER` does not change: `cortex project`, whose work the registry is. Its acceptance criteria say *read or written*, as the text does since phase 2.
- **`cortex sync --locked` registers**, as any sync does (§3.3): the option is about the lock, not the machine, and a CI runner sets `CORTEX_NO_REGISTER` — the guide says so.
- **A project already listed resolves no path** (§3.3): the registering sync finds it as written, phase 1's comparison, before it looks at any directory.
- **The suite's Cortex home** is the test process's too, not only the commands': a test that calls the modules never reads the machine's registry.

### While phase 3 was implemented (2026-11-04)

- **The order of `prune`** (§3.4): the entries gone are dropped first, in one write, so that a registry that cannot be written removes no version.
- **"Empty" means no entry** (§3.4): a registry whose every project is gone has its entries dropped, and prunes on.
- **An `unreadable` entry keeps the versions that can be read** (§3.4), as phase 1 widened `unreadable`.

After the counter-review of 2026-11-05:

- **A `no-project` entry is not always gone** (§3.4): when its `cortex.local.toml` still reads a version, it stays, with that version. A checkout of a branch without Cortex, a stash of an uncommitted pin, or a rebase made `prune` drop it and remove the version, and the next sync failed offline. `cortex project list` gives that version as what the entry reads, and `check` says it. One whose `cortex.local.toml` is there and cannot be read is `unreadable`, and stays too: what it reads is not known, which is no proof that the project is gone.
- **The registry is read again just before the removals** (§3.4): a project a sync registered while `prune` read the others keeps its versions, and is counted. What is left of the race is in §5.
- **The instructions file has a rule for a spec that is gone** (§5): *the directory `spec` names does not exist* → `cortex sync`. A project the assistant never synced — already on its version — was not registered, and the first `prune` with no `DIR` left its spec naming a directory that no rule caught. A `cortex/` that links to a checkout moved or deleted is removed first: the sync refuses it, as a link Cortex did not make — a message that names the wrong cause, #303. `cortex upgrade` to 1.5.0 writes the rule in, where no one edited the file.
- **`CORTEX_NO_REGISTER` does not change `prune`** (§3.4): said, as phase 2 says what it changes.
- **The tests compare a version list sorted**, since a binary's own version sorts anywhere among them; and that `prune DIR` writes no registry is checked by its time too, not only its bytes.

### While phase 4 was implemented (2026-11-04)

- **What `upgrade --all` refuses once, up front** (§3.5): a `VERSION` this cortex does not serve; and the argument errors, exit 2 — `--instructions-file` with `--all`, `--commit` and `--message` apart, a branch name git refuses. Without git, `--branch` and `--commit` are refused before anything changes.
- **An empty registry moves nothing**, exit 0, and a skipped project does not make the exit 1 (§3.5).
- **When the branch is made** (§3.5): once the project's checks pass, before its first write; none for a project with nothing to write; one made by the run serves the repository's other projects; a project refused afterwards keeps it.
- **Where `--commit` commits** (§3.5): in each project the upgrade wrote files in, finishing an upgrade cut short included; not where git does not track `cortex.toml`, nor where it ignores a written file; a commit git refuses is that project's refusal.

After the counter-review of 2026-11-05:

- **`--commit` never commits the developer's own changes** (§3.5): `cortex.toml` and the bootstrap files are read before the upgrade writes; where git sees a change — staged or not, or a file untracked — in the pin, or in a bootstrap file the upgrade then writes, nothing is committed there, and the command says which, to commit by hand. A local `theme = "none"` no longer lands in the team's commit, and a `cortex.toml` staged in part stays so. The bootstrap counts because the upgrade writes it only where it is Cortex's template: a template restored over a team's edit — `cortex init --force`, which keeps `cortex.toml` — is written again, and, committed, would undo the edit in HEAD under the upgrade's message. The lock does not count, decided by the maintainer: written whole from the pin, it is the same whatever it held, so a lock a 1.4.0 sync wrote and nobody committed is committed. A git that cannot tell stops the commit, never the upgrade.
- **A file outside the work tree** — a bootstrap linked to a shared file, or named by `--instructions-file` — is written, said, and left out of the commit. Read before the upgrade, it had git refuse the project, upgrade and all.
- **Run again with nothing to write**, a project whose `cortex.toml`, lock or bootstrap files git still sees changed — a run cut short, a commit refused — names them, rather than *nothing to commit*; interrupted, `--all` says that `git status` in the project cut short says what it left to commit.
- **The dry run refuses a linked work tree** of a repository it would make the branch in, as the run does.
- **The branch is checked for only where there is something to commit** (§3.5): the check that `NAME` exists moved from before the project's upgrade to where the branch is made, after its checks — the dry run makes it there too. A project already on `NAME` is written there. So the same `--all --branch NAME --commit` run again, once a refused project is fixed, refuses none of those it moved.
- **Interrupted, `--all` still ends with its summary** (§3.5): the project cut short is said — with its branch, when one was made for it, and `git status` to see what was written — the projects not reached are said, and the command exits 130. One project interrupted after its branch was made says so too.
- **A commit git refuses** — a hook, a signature it cannot make — says that git refused it, with what git said, and that the files stay staged, ready to commit by hand.
- **`--branch` takes a name as it is written**: `git check-ref-format refs/heads/NAME` as well as `--branch`, which expands `@{-1}` to the branch it names; such a name is an argument error, exit 2.
- **The edges of git** (§3.5): a branch the run made in one work tree is said to be checked out there when a linked work tree of the same repository comes next, rather than *exists already*; a project that is a submodule says, once committed, that its superproject shows it changed; where git ignores every file the upgrade writes, no branch is made, and the command says there is nothing to commit rather than *must commit*.

### 2026-11-08 — ADR-013, the configuration cascade

Carried by [#334](https://github.com/xod-labs/cortex/issues/334), for 1.8.0.

- **§3.2 — `PINNED`** is the team's version ([ADR-013](ADR-013-configuration-cascade.md) §3.1): a committed `cortex.toml`'s, else the team's file's, read loosely of a project this cortex cannot read whole; `--json` adds its origin.
- **§3.4 — `cortex prune`** keeps each project's candidate versions — `cortex.toml`'s, the team's file's, a try — and the version of the spec read now.
- **§3.5 — `--all`** moves the projects that pin their own version; one that follows the team's file is skipped, *follows the team's 1.8.0*, exit 0. `--all --team` groups the projects by their team's folder, checks every follower's overlays, then moves each team's file once (ADR-013 §3.11).
