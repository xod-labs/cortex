# ADR-010 — `cortex.lock`, `cortex upgrade` and the bootstrap marker

- **Status:** Proposed
- **Date:** 2026-11-03 (proposed)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `cli/cortex_cli/` (`sync`, `init`, `validate`, the store, two new commands `upgrade` and `prune`), `core/cortex_core/project.py` (the grammar of `cortex.lock`), `templates/` (the two bootstrap templates), the init parity matrix, `README.md`, `docs/getting-started.md`, `docs/extending-layers.md`, `docs/migrating-to-the-binary.md`
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) — §3.3 (the store), §3.4 (`cortex.toml`), §3.5 (`cortex sync`), §3.7 (`cortex init` and the bootstrap file) and §7, which left the lock, the upgrade and the pruning to this record; ADR-008 records it in its own amendments when this ADR is implemented · roadmap epic [#99](https://github.com/xod-labs/cortex/issues/99) · [#141](https://github.com/xod-labs/cortex/issues/141), folded in on 2026-11-03

---

## 1. Context

ADR-008 gave every project a committed `cortex.toml` that pins one released version of the spec, and every machine one store that holds each version once. It stopped there on purpose (§7): *"`cortex.lock` and upgrade tooling — a resolved manifest, `cortex upgrade`, pruning unused versions from the store — #99."*

Since then, every merge into `main` is a release (ADR-009): seven releases in October, three on 2026-11-03 alone. Moving a project is still done by hand, and three gaps show:

- **Nothing records what a project actually ran with.** `cortex.toml` names a version, and the store holds whatever arrived under that name. A version is checked against its release's `SHA256SUMS` once, when it is downloaded, and against the table of checksums a binary embeds — which knows only the releases before that binary. Nothing a team commits says *which bytes* that version was, so nothing can tell a teammate's spec from yours, or a store altered after the download from the one the team used.
- **Moving a project to another version is four manual steps** (`docs/extending-layers.md`, *Upgrade workflow*): edit `version`, run `cortex sync`, run `cortex validate`, then read the changelog to learn what moved. Nothing says which roles, capabilities or workflows the new version adds, removes or changes, nor whether the project's overlays still find the base files they extend — until `validate` fails after the switch. And the bootstrap file stays as the old version wrote it: `cortex init` keeps an existing one unless `--force`, which replaces it, even when the team adapted it.
- **`cortex init` guesses what a kept bootstrap file is from its text** (#141). It compares the file's first line with the template's to tell the two layouts apart, and searches for `cortex.local.toml` to tell an older bootstrap from a current one. A title a team adapted — `# Cortex AI Team — Workspace Mode (Acme)` — is taken for the other layout, and every `cortex init` then suggests the `--force` that would replace the team's file. To know whether a project's bootstrap must be written again, an upgrade needs what that guess cannot give: the contract version the file was written for.

The store grows by one directory per version a machine ever synced. A version is under a megabyte, so this is tidiness rather than space, but nothing removes one, and ADR-008 deliberately keeps no list of the projects that could say which versions are still used.

## 2. Decision

**A project commits a `cortex.lock`, `cortex upgrade` moves it to another version, `cortex prune` removes the versions a machine's projects no longer use, and each bootstrap file says what it is in a marker.**

- **`cortex.lock`** sits beside `cortex.toml` and is committed. It records the version the project resolved and the **digest of that version's spec**: one SHA-256 over every file of its three trees. `cortex sync` writes it, and checks the store against it: a spec whose files differ from what the team locked is not used.
- **`cortex upgrade [VERSION]`** moves the project to another version, by default the binary's own. Before it changes anything, it says what changes — the agents added, removed and changed, layer by layer — and checks the project's overlays against the new version. It then writes `version`, syncs, and writes the bootstrap file again when the new version's format is newer and the file is the one an older version wrote, unchanged.
- **`cortex prune DIR...`** finds the projects under the directories it is given, keeps the versions they pin and the binary's own, and removes every other version from the store. It keeps no list: the user says where the projects are.
- **Each bootstrap template carries a marker**, `<!-- cortex:bootstrap layout=single format=1 -->`, on its own line under the title. `layout` says which layout the file was written for, and `format` which contract version. `cortex init` and `cortex upgrade` read the marker, never the text around it.

Out of scope: the digests of images a project depends on, which #99 listed, wait for a project to declare images (§7); the configuration cascade and the IDE modes (#98); the multi-project registry (ADR-021, #48); a `cortex` that replaces its own binary — the install script stays the way to upgrade the binary.

## 3. Detailed contract

### 3.1 The bootstrap marker

Each bootstrap template carries exactly one marker line, the first line after its title and a blank line:

```markdown
# Cortex AI Team — Workspace Mode

<!-- cortex:bootstrap layout=workspace format=1 -->
```

- **The form is fixed**: the whole line is `<!-- cortex:bootstrap layout=L format=N -->`, with `L` one of `single` or `workspace` and `N` a positive integer without leading zeros. Anything else on the line, or another spelling, is no marker.
- **It is read anywhere in the file**, as a whole line: a team may add lines above it, or change the title. A file with two marker lines has none — the command says so.
- **`layout`** is the layout of the template that wrote the file. It replaces the comparison of first lines.
- **`format`** is the bootstrap's contract version: it is raised when a template changes what a project's file must say for the spec to be read correctly — a new rule the LLM must follow, a renamed file it must read — and not for a rewording. It replaces the search for `cortex.local.toml`. ADR-010's templates are format 1.
- The templates and the docs ask teams to keep the line as it is. HTML comments do not render, so the file reads the same in a Markdown viewer.

**What `cortex init` says of a kept file** — one note, the first that applies, as today:

1. no marker, and neither the `# Cortex AI Team` heading nor a personality block → *no Cortex bootstrap*;
2. no marker, with that heading or that block → *a bootstrap written before the marker* — an older one;
3. a marker of the other layout → *written for a single project* or *for a workspace*;
4. a marker of an older format → *an older bootstrap*, naming both formats;
5. a personality block that no longer fits the theme → as today.

`cortex init` never adds the marker to a kept file: it writes a file only with `--force`, as ADR-008 §3.7 says. The note says what `--force` does, as today.

### 3.2 `cortex.lock`

```toml
# Written by `cortex sync`: the spec this project resolved. Committed — do not edit.
format = 1
version = "1.4.0"
digest = "sha256:4f6d…"
```

- **Beside `cortex.toml`**, at the project root — the workspace root in workspace mode — and **committed**, like `cortex.toml`. A workspace root that is no git repository keeps it per developer, as it keeps `cortex.toml` (ADR-008 §9).
- **`format`** is the lock file's own format, an integer, 1 here. A lock of a later format is refused, with the command that upgrades the binary.
- **`version`** is the version the lock was written for. It equals `cortex.toml`'s `version` once synced.
- **`digest`** is the spec's digest: `sha256:` and 64 lowercase hex digits.
- **The grammar is the core's** (`cortex_core.project`), as for the two other files: every key required, an unknown key an error.
- **The spec's digest** is computed from the files of a version directory, the three trees `agents/`, `templates/`, `docs/`:
  - each regular file, its path relative to the version directory with `/` separators;
  - one line per file, `<SHA-256 of its bytes, lowercase hex>  <path>\n`, in the order of the paths' UTF-8 bytes — `sha256sum`'s format;
  - the digest is the SHA-256 of those lines.

  It depends on the files' bytes only: the same version gives the same digest whether it was downloaded, written from the binary's embedded archive or extracted on another system.

**Why the spec only.** A lock pins what the repository does not hold. The project's overlays are its own files, already in git; recording their digests would make the lock change with every overlay edit, for nothing git does not already say. What the overlays *resolve to* is derived from the two — the spec, which the lock pins, and the overlays, which git pins.

### 3.3 `cortex sync` and the lock

After it has the pinned version in the store, `cortex sync`:

| `cortex.lock` | What sync does |
|---|---|
| missing | writes it, and says: *cortex.lock written — commit it* |
| of the pinned version, same digest | nothing |
| of the pinned version, another digest | puts the version in the store again — from the embedded archive, or downloaded and checked as in ADR-008 §3.3 — and compares again. Still different: refuses, exit 1, naming both digests, and changes nothing in the project. |
| of another version | rewrites it for the pinned version, and says: *cortex.lock now names X — commit it*. The pin moved by hand, or by `cortex upgrade` on another branch. |
| of a later format, or malformed | refuses, exit 1, naming the file and what is wrong |

- **`--from PATH`** syncs a checkout, which has no version: the lock is neither read nor written, and sync says so.
- **The digest is computed from the store**, once per sync: a version is under a megabyte, and the cost is that of reading it.
- **`cortex init` writes the lock** through the sync it runs.

### 3.4 `cortex validate` and the lock

`cortex validate` reports, as warnings:

- no `cortex.lock` — *run `cortex sync`, then commit it*;
- a `cortex.lock` of another version than `cortex.toml`'s;
- a spec whose digest is not the lock's.

They are warnings because a later binary warns where an earlier one passed, within a major version (ADR-008 §9): a project of 1.3 has no lock, and must still validate. `--strict` fails on them, so a CI that runs it — and installs the binary of the version `cortex.toml` pins — checks the lock.

### 3.5 `cortex upgrade`

```text
cortex upgrade [VERSION] [--dry-run] [--force] [--instructions-file PATH]
```

In the project of the current directory — the nearest `cortex.toml`, as `cortex sync` finds it:

1. **The target** is `VERSION`, or the binary's own version. A version the binary does not serve is refused as by `cortex sync`: a newer one with the command that upgrades the binary, one before 1.0.0 with the versions served. A malformed one exits 2. The target is the pinned version → *already on X*, and only step 5 runs.
2. **The target is put in the store**, downloaded and checked if need be, as by `cortex sync`.
3. **What changes is said**, comparing the two versions' trees:
   - for each layer — roles, capabilities, personalities, workflows — the files added, removed and changed, by name;
   - the templates changed, and the number of other files that changed;
   - *upgrade* or *downgrade*, and where the release notes are.
4. **The overlays are checked against the target**: the validator of ADR-007 runs with the target as the base. If it reports an error — an overlay whose `Base:` the target removed, for instance — `cortex upgrade` prints the report and stops, exit 1, having changed nothing. `--force` goes on regardless.
5. **The bootstrap file.** The instructions file is found as `cortex init` finds it, or named by `--instructions-file`. Its marker is read:
   - the target's template has the same `format`, or an older one → nothing;
   - a newer one, and the file is exactly what the pinned version's template writes for that layout and theme → it is written again from the target's template;
   - a newer one, and the file differs — a team adapted it, or it has no marker → it is kept, and the command says so with `cortex init --force`, which writes it again and keeps it as `FILE.bak`.
6. **The switch**: `version` is written in `cortex.toml`, keeping its comments, then the project is synced — `cortex.local.toml`, the link or copy, and `cortex.lock`. If the sync fails, `version` is written back.
7. **It ends** with what to commit — `cortex.toml`, `cortex.lock`, and the bootstrap file when it was written — and that teammates run `cortex sync` after they pull.

`--dry-run` runs steps 1 to 5 and changes nothing in the project; the store may gain the target version. The exit code is that of a real run: 1 when step 4 would stop it.

### 3.6 `cortex prune`

```text
cortex prune DIR... [--keep VERSION]... [--dry-run]
```

- **It finds the projects under each `DIR`**: every `cortex.toml` up to six directories down, skipping `.git`, `node_modules`, `vendor`, `.venv` and the `cortex/` a link or copy sync made. A `cortex.toml` it cannot read is named, and its version is not known.
- **It keeps** the versions those projects pin, the binary's own version, and each `--keep`. It removes every other version from the store, and lists both: the versions kept, each with the projects that pin it, and the versions removed.
- **No `DIR`** exits 2: there is no list of projects to fall back on (ADR-008 §3.3), and pruning without one would remove the versions of every project it was not shown.
- **A version is removed by a rename first** — out of `versions/`, into a staging the store already cleans — then deleted, so that a sync running at the same time finds it whole or absent. A project whose version was removed gets it back at its next `cortex sync`, checked against its `cortex.lock`.
- **`--dry-run`** lists, and removes nothing.
- The runtime reads the store too (ADR-008 §3.6). A version only the runtime uses is kept with `--keep`.

### 3.7 What stays as it is

- `cortex.toml` and `cortex.local.toml`: no new key. The layout is read from the bootstrap marker, not stored.
- The store's layout: a version directory still holds its three trees and nothing else. Nothing is per project, and nothing lists the projects.
- The binary is still upgraded by the install script. `cortex upgrade` moves a project, never the binary.
- The runtime does not read `cortex.lock` (§7).

## 4. Phases

Execution order: **1 → 2 → 3 → 4**. All four ship in one release, 1.4.0: new commands and a new committed file, nothing a project must change.

### Phase 1 — The bootstrap marker

The marker in both templates; `cortex init` reads a kept file's layout and format from it (§3.1); the init parity matrix re-captured.

Acceptance criteria:

- both bootstrap templates carry exactly one marker line, the third, with their layout and `format=1`
- the init parity matrix, re-captured, differs from the previous one by that line in every bootstrap file it holds, and in nothing else
- a kept workspace bootstrap whose title a team changed to `# Cortex AI Team — Workspace Mode (Acme)` gets no layout note from `cortex init --workspace` — the case of #141
- a kept file whose marker names the other layout gets the layout note; one of an older format gets the older-bootstrap note naming both formats; one with two markers is said to have none
- a kept file with no marker but the Cortex heading gets the *written before the marker* note; one with neither gets the *no Cortex bootstrap* note
- `cortex init` without `--force` leaves a kept file byte for byte, marker or not

### Phase 2 — `cortex.lock`

The digest of a spec version, the lock's grammar in the core, `cortex sync` writing and checking it (§3.2, §3.3), `cortex validate`'s warnings (§3.4).

Acceptance criteria:

- `cortex init` and `cortex sync` write `cortex.lock` with `format = 1`, the pinned version and the spec's digest
- the digest of a version written from the embedded archive equals that of the same version downloaded
- a file changed in the store's copy of the pinned version makes `cortex sync` put the version in the store again, and the lock then matches; a release whose spec differs from the lock's digest makes it refuse, exit 1, naming both digests, with the project unchanged
- a lock of another version is rewritten, and said; `cortex sync --from` neither reads nor writes it
- a lock with an unknown key, a malformed digest or `format = 2` is refused, naming the file and the key
- `cortex validate` warns on a missing lock, a lock of another version and a digest that differs, and `--strict` fails on each; a project of 1.3 with no lock still validates without `--strict`

### Phase 3 — `cortex upgrade`

The command of §3.5.

Acceptance criteria:

- on a project pinned to an older version, `cortex upgrade` moves it to the binary's version: `version` is written with the file's comments kept, the spec synced and the lock rewritten; the output names the agents added, removed and changed, by layer
- `cortex upgrade X` to an older version says *downgrade*, and moves it; to the pinned version says *already on X* and changes nothing
- a version newer than the binary is refused with the upgrade command; a malformed one exits 2
- `--dry-run` leaves every file of the project byte for byte
- an overlay whose `Base:` the target removed stops the upgrade, exit 1, with the validator's report and the project unchanged; `--force` moves it anyway
- a bootstrap file exactly as the pinned version wrote it, of an older format than the target's, is written again; one a team changed is kept, and the command names `cortex init --force`
- a sync that fails after `version` was written leaves `cortex.toml` as it was

### Phase 4 — `cortex prune`

The command of §3.6.

Acceptance criteria:

- with projects under `DIR` pinned to A and B, and a store holding A, B, C and the binary's own version, `cortex prune DIR` removes C only, and lists A and B with their projects
- `--keep C` keeps C; `--dry-run` removes nothing; the binary's own version is never removed
- no `DIR` exits 2; a `cortex.toml` inside `node_modules/` or a synced `cortex/` is not counted; one that cannot be read is named
- a project whose version was pruned gets it back at its next `cortex sync`, and the lock still matches

## 5. Consequences

### Positive

- **A team commits what its agents ran with**: one digest that a teammate's machine, a CI runner or an auditor recomputes from the spec they have. A spec altered after download, or a release served differently from the one locked, is refused instead of used.
- **Upgrading is one command that says what it does first**: the agents that change, the overlays that would break, the bootstrap file that must follow — before anything is written.
- **The bootstrap file says what it is**: a team can retitle and extend it, and `cortex init` still knows its layout and format. #141 is closed by the same marker an upgrade needs.
- **The store can be emptied of old versions** without a registry, and safely: a version pruned by mistake comes back at the next sync, checked against the lock.

### Negative

- **One more committed file**, and one more line in every bootstrap file. A project's first sync with 1.4.0 adds `cortex.lock` as an uncommitted change.
- **Every sync reads the pinned version once more**, to compute its digest: under a megabyte of reading.
- **A modified bootstrap file is never rewritten by `cortex upgrade`.** The team carries its changes over by hand after `cortex init --force`, as today.
- **`cortex prune` is only as good as the directories it is shown.** A project elsewhere loses its version, and gets it back at its next sync, which needs the network.

### Neutral

- Teammates on 1.3.x ignore `cortex.lock`: it is a file they do not read.
- `format` will be raised rarely: a rewording of a template keeps it.

## 6. Alternatives considered

- **A lock of every resolved agent, overlays included** — the base file and each overlay tier, with their digests. Rejected: the overlays are in git already, and the lock would change with every overlay edit. What they resolve to follows from the spec, which the lock pins, and the overlays.
- **The checksum of the spec archive instead of a digest of the files.** Rejected: a binary writes its own version from its embedded archive, built on each target with that runner's `git` and `gzip`, so the archive's bytes are not guaranteed to be the release's. The files are, and the digest of the files can be computed again from the store at any time.
- **A store-wide list of the projects, or of when each version was last used**, for pruning. Rejected: ADR-008 keeps no list of projects (§3.3), and the registry is ADR-021's (#48). Last use is no proof of no use: in `store` mode a project's assistant reads the store daily without ever running `cortex sync`.
- **`cortex upgrade` rewriting a modified bootstrap file and keeping a backup**, as `cortex init --force` does. Rejected: that is the replacement #141 complained of. An upgrade rewrites only what nobody changed.
- **`cortex upgrade` upgrading the binary too.** Rejected: the install scripts check the binary's `SHA256SUMS` and know where to put it on each system; a binary that replaces itself does that twice, on Windows while it runs.
- **The layout in `cortex.toml`.** Rejected: the layout belongs to the file that was written for it, and a key there would be a second source that can disagree with the file.

## 7. Follow-ups (out of scope for this ADR)

1. **The digests of the images a project depends on**, listed in #99: no project declares an image yet. The ADR that binds a project to the runtime's images extends `cortex.lock` with a `format` of its own.
2. **The runtime checking the lock**: a run that records the digest it ran with, and refuses a spec whose digest differs.
3. **The epics after this one shift by one number**: the epics numbered 010 to 022 on 2026-10-30 become 011 to 023, as ADR-002 and ADR-008 record in amendments.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.3 the store, §3.4 `cortex.toml`, §3.5 `cortex sync`, §3.7 `cortex init`, §7 the follow-ups this record takes up, §9 the version rule for later checks
- [ADR-009](ADR-009-release-cycle.md) — every merge a release, and the pace of versions that makes upgrading a routine
- [ADR-007](ADR-007-cortex-core.md) — the validator that checks overlays against a base
- Issues: [#99](https://github.com/xod-labs/cortex/issues/99), [#141](https://github.com/xod-labs/cortex/issues/141)

## 9. Amendments

None yet.
