# ADR-010 — `cortex.lock`, `cortex upgrade` and the bootstrap marker

- **Status:** Proposed
- **Date:** 2026-11-03 (proposed)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `cli/cortex_cli/` (`sync`, `init`, `validate`, the store, three new commands `upgrade`, `prune` and `self-update`), `core/cortex_core/project.py` (the grammar of `cortex.lock`), `templates/` (the two bootstrap templates), the init parity matrix, `README.md`, `docs/getting-started.md`, `docs/extending-layers.md`, `docs/migrating-to-the-binary.md`
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) — §3.3 (the store), §3.4 (`cortex.toml`), §3.5 (`cortex sync`), §3.7 (`cortex init` and the bootstrap file) and §7, which left the lock, the upgrade and the pruning to this record; ADR-008 records it in its own amendments when this ADR is implemented · roadmap epic [#99](https://github.com/xod-labs/cortex/issues/99) · [#141](https://github.com/xod-labs/cortex/issues/141), folded in on 2026-11-03

---

## 1. Context

ADR-008 gave every project a committed `cortex.toml` that pins one released version of the spec, and every machine one store that holds each version once. It stopped there on purpose (§7): *"`cortex.lock` and upgrade tooling — a resolved manifest, `cortex upgrade`, pruning unused versions from the store — #99."*

Since then, every merge into `main` is a release (ADR-009): seven releases in October, three on 2026-11-03 alone. Moving a project is still done by hand, and four gaps show:

- **Nothing records what a project actually ran with.** `cortex.toml` names a version, and the store holds whatever arrived under that name. A version is checked against its release's `SHA256SUMS` once, when it is downloaded, and against the table of checksums a binary embeds — which knows only the releases before that binary. Nothing a team commits says *which bytes* that version was, so nothing can tell a teammate's spec from yours, or a store altered after the download from the one the team used.
- **Moving a project to another version is four manual steps** (`docs/extending-layers.md`, *Upgrade workflow*): edit `version`, run `cortex sync`, run `cortex validate`, then read the changelog to learn what moved. Nothing says which roles, capabilities or workflows the new version adds, removes or changes, nor whether the project's overlays still find the base files they extend — until `validate` fails after the switch. And the bootstrap file stays as the old version wrote it: `cortex init` keeps an existing one unless `--force`, which replaces it, even when the team adapted it.
- **`cortex init` guesses what a kept bootstrap file is from its text** (#141). It compares the file's first line with the template's to tell the two layouts apart, and searches for `cortex.local.toml` to tell an older bootstrap from a current one. A title a team adapted — `# Cortex AI Team — Workspace Mode (Acme)` — is taken for the other layout, and every `cortex init` then suggests the `--force` that would replace the team's file. To know whether a project's bootstrap must be written again, an upgrade needs what that guess cannot give: the contract version the file was written for.
- **Following a teammate is a line to copy.** A project pinned to a version newer than a teammate's binary stops their `cortex sync`, which prints the install script's command to paste into a shell. And the bootstrap file tells the assistant to ask the user to run `cortex sync`, not to run it: after every pull that moved the pin, each step is the user's to type.

The store grows by one directory per version a machine ever synced. A version is under a megabyte, so this is tidiness rather than space, but nothing removes one, and ADR-008 deliberately keeps no list of the projects that could say which versions are still used.

## 2. Decision

**A project commits a `cortex.lock`, `cortex upgrade` moves it to another version, `cortex self-update` moves the binary, `cortex prune` removes the versions a machine's projects no longer use, and each bootstrap file says what it is in a marker — and asks the assistant to keep the machine in step with the project.**

- **`cortex.lock`** sits beside `cortex.toml` and is committed. It records the version the project resolved and the **digest of that version's spec**: one SHA-256 over every file of its three trees. `cortex sync` writes it, and checks the store against it: a spec whose files differ from what the team locked is not used.
- **`cortex upgrade [VERSION]`** moves the project to another version, by default the binary's own. Before it changes anything, it says what changes — the agents added, removed and changed, layer by layer — and checks the project's overlays against the new version. It then writes `version`, syncs, and writes the bootstrap file again when the new version's format is newer and the file is the one an older version wrote, unchanged.
- **`cortex prune DIR...`** finds the projects under the directories it is given, keeps the versions they pin and the binary's own, and removes every other version from the store. It keeps no list: the user says where the projects are.
- **Each bootstrap template carries a marker**, `<!-- cortex:bootstrap layout=single format=1 -->`, on its own line under the title. `layout` says which layout the file was written for, and `format` which contract version. `cortex init` and `cortex upgrade` read the marker, never the text around it.
- **`cortex self-update [VERSION]`** replaces the binary with a release's — the latest by default — downloaded and checked as the install script does it. Every refusal of a version newer than the binary names it, and `cortex upgrade` says when a release newer than the binary is out.
- **The bootstrap asks the assistant to keep the machine in step**: a spec missing, or of another version than `cortex.toml`'s, and it runs `cortex sync`; a binary too old for the pin, and it runs `cortex self-update`, then `cortex sync`. It says what it runs, its tool asks the user first when set to, and it never moves the project's pin.

Out of scope: the digests of images a project depends on, which #99 listed, wait for a project to declare images (§7); the configuration cascade and the IDE modes (#98); a registry of a machine's projects, which a record of its own adds (§7); the multi-project runtime (ADR-021, #48); the first install of the binary, which stays the install script's.

## 3. Detailed contract

### 3.1 The bootstrap marker

Each bootstrap template carries exactly one marker line, the first line after its title and a blank line, followed by a comment that asks to keep it:

```markdown
# Cortex AI Team — Workspace Mode

<!-- cortex:bootstrap layout=workspace format=1 -->
<!-- Keep the line above as it is: `cortex init` and `cortex upgrade` read it. -->
```

- **The form is fixed**: the whole line is `<!-- cortex:bootstrap layout=L format=N -->`, with `L` one of `single` or `workspace` and `N` a positive integer without leading zeros. Anything else on the line, or another spelling, is no marker.
- **It is read anywhere in the file**, as a whole line: a team may add lines above it, or change the title. A file with two marker lines has none — the command says so.
- **`layout`** is the layout of the template that wrote the file. It replaces the comparison of first lines.
- **`format`** is the bootstrap's contract version: it is raised when a template changes what a project's file must say for the spec to be read correctly — a new rule the LLM must follow, a renamed file it must read — and not for a rewording. It replaces the search for `cortex.local.toml`. ADR-010's templates are format 1.
- The templates, in the comment under the marker, and the docs ask teams to keep the line as it is. HTML comments do not render, so the file reads the same in a Markdown viewer.

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

1. **The target** is `VERSION`, or the binary's own version. A version the binary does not serve is refused as by `cortex sync`: a newer one naming `cortex self-update` (§3.7), one before 1.0.0 with the versions served. A malformed one exits 2. The target is the pinned version → *already on X*, and only step 5 runs.
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

**A release newer than the binary.** With no `VERSION`, `cortex upgrade` asks where the releases are which version is the latest — a few seconds at most — and, when it is newer than the binary, says so: *Cortex 1.5.0 is out — `cortex self-update`, then `cortex upgrade`, moves the project to it*. Offline, or with a `VERSION`, it says nothing of it. It never moves the binary itself.

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

### 3.7 `cortex self-update`

```text
cortex self-update [VERSION] [--dry-run]
```

1. **The target** is `VERSION`, or the latest release — the version its `latest/download/SHA256SUMS` redirects to; where the releases are served without that redirect, the command asks for a `VERSION`. A malformed one exits 2. The binary's own version → *already on X*, and nothing is downloaded. An older one is *a downgrade*: it is installed, and the command says that a project pinned to a later version is then refused — and, before 1.4.0, that the binary it installs has no `self-update`.
2. **The binary replaced is the one running**, under whichever name it was installed — `cortex` or `cortex-ai`. A source checkout runs no binary: it is refused, with the install script's command. So is a directory this user cannot write in — a binary an administrator or a package manager installed.
3. **The download** is the release asset of this system — `cortex-linux-x86_64.tar.gz`, `cortex-linux-aarch64.tar.gz`, `cortex-macos-arm64.tar.gz` or `cortex-windows-x86_64.zip` — from where the store and the install scripts download, `CORTEX_RELEASES_URL` included, checked against the release's `SHA256SUMS`. A mismatch, or an asset that holds no binary, is refused, and nothing is replaced.
4. **The new binary runs before it is installed**: written beside the old one, on the same file system, it must answer `--version` with the target. Otherwise it is removed, and the old one stays.
5. **The replacement is a rename.** On Linux and macOS the new binary is renamed over the old one; the running process keeps the file it started from. Windows refuses to overwrite a running executable, not to rename it: the old one is renamed `NAME.old`, the new one takes its name, and the next `cortex` that starts removes the old one. A failure between the two renames puts the old one back.
6. **It ends** with the version installed, and what it did not do: no project moved — `cortex upgrade` moves one.

`--dry-run` resolves the target, downloads it, checks it and runs it, and replaces nothing.

**Where it is named.** Every refusal of a version newer than the binary — a pinned version in `cortex sync` and `cortex init`, a target of `cortex upgrade`, a `cortex.lock` of a later format — names `cortex self-update` instead of the install script's command. A binary before 1.4.0 still names the install script, which installs the latest release: the first move to a binary with `self-update` is the install script's.

### 3.8 The bootstrap keeps the machine in step

The first rules of both bootstrap templates — what the assistant does before anything else — become actions it takes, where they asked the user to act:

| The assistant finds | It |
|---|---|
| no `cortex.local.toml`, or no `spec` in it | runs `cortex sync` at the project root — the workspace root in workspace mode |
| a spec of another version than `cortex.toml`'s | runs `cortex sync` |
| `cortex sync` refusing a version newer than the binary | runs `cortex self-update`, then `cortex sync` again |
| no `cortex` command — nor `cortex-ai` | tells the user how to install it, and goes no further |

- **It says what it runs, and why, before it runs it.** Whether the user approves each command is the setting of the assistant's tool, not the bootstrap's: a tool set to ask, asks. An assistant that cannot run commands asks the user to run them.
- **It never moves the project's pin**: `cortex upgrade`, or an edit of `version`, is the team's decision, made in a commit.
- **It goes no further until the sync succeeded**: it does not work from a spec of another version than the one the project pins.

These rules are part of format 1 (§3.1): no released template carried a marker before them.

### 3.9 What stays as it is

- `cortex.toml` and `cortex.local.toml`: no new key. The layout is read from the bootstrap marker, not stored.
- The store's layout: a version directory still holds its three trees and nothing else. Nothing is per project, and nothing lists the projects.
- `cortex upgrade` moves a project, never the binary; `cortex self-update` moves the binary, never a project. The install script stays the way to install the binary the first time.
- The runtime does not read `cortex.lock` (§7).

## 4. Phases

Execution order: **1 → 2 → 3 → 4 → 5**. All five ship in one release, 1.4.0: new commands and a new committed file, nothing a project must change.

### Phase 1 — The bootstrap marker

The marker in both templates; `cortex init` reads a kept file's layout and format from it (§3.1); the init parity matrix re-captured.

Acceptance criteria:

- both bootstrap templates carry exactly one marker line, the third, with their layout and `format=1`, and the comment that asks to keep it, the fourth
- the init parity matrix, re-captured, differs from the previous one by those two lines in every bootstrap file it holds, and in nothing else
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

### Phase 5 — `cortex self-update`, and the bootstrap keeps the machine in step

The command of §3.7 and the refusals that name it, the notice of `cortex upgrade` (§3.5), the rules of §3.8 in both bootstrap templates; the init parity matrix re-captured.

Acceptance criteria:

- `cortex self-update` replaces the binary with the latest release's, checked against its `SHA256SUMS`; `cortex self-update X` with X's; on the binary's own version it says *already on X* and downloads nothing
- a checksum mismatch, an asset that holds no binary, or a new binary that does not answer `--version` with the target, leaves the old binary byte for byte
- a running executable is replaced on every target — on Windows renamed aside, and the `.old` it leaves removed by the next run
- a source checkout, and a directory the user cannot write in, are refused with the install script's command; `--dry-run` replaces nothing; a malformed version exits 2
- `cortex sync` on a project pinned to a version newer than the binary names `cortex self-update`, and so does `cortex upgrade` given such a version
- with no `VERSION`, `cortex upgrade` says when a release newer than the binary is out, and says nothing of it offline
- both bootstrap templates have the assistant run `cortex sync`, and `cortex self-update` when sync asks for it, never `cortex upgrade`; the init parity matrix, re-captured, differs by those lines only

## 5. Consequences

### Positive

- **A team commits what its agents ran with**: one digest that a teammate's machine, a CI runner or an auditor recomputes from the spec they have. A spec altered after download, or a release served differently from the one locked, is refused instead of used.
- **Upgrading is one command that says what it does first**: the agents that change, the overlays that would break, the bootstrap file that must follow — before anything is written.
- **The bootstrap file says what it is**: a team can retitle and extend it, and `cortex init` still knows its layout and format. #141 is closed by the same marker an upgrade needs.
- **The store can be emptied of old versions** without a registry, and safely: a version pruned by mistake comes back at the next sync, checked against the lock.
- **Following a teammate takes no command typed**: after a pull that moved the pin, the assistant syncs, and updates the binary when the pin asks for it — saying so, and asking first where the user's tool is set to ask. A user who runs the commands by hand has one each: `cortex self-update`, `cortex sync`.

### Negative

- **One more committed file**, and one more line in every bootstrap file. A project's first sync with 1.4.0 adds `cortex.lock` as an uncommitted change.
- **Every sync reads the pinned version once more**, to compute its digest: under a megabyte of reading.
- **A modified bootstrap file is never rewritten by `cortex upgrade`.** The team carries its changes over by hand after `cortex init --force`, as today.
- **`cortex prune` is only as good as the directories it is shown.** A project elsewhere loses its version, and gets it back at its next sync, which needs the network.
- **A binary that replaces itself repeats the install scripts' checks** in Python — the asset of this system, its `SHA256SUMS` — and leaves, on Windows, a `.old` beside the new binary until the next run.
- **The assistant runs commands on its own** — `cortex sync`, `cortex self-update` — within what the user's tool allows: a tool set to run commands unasked downloads a binary unasked, checked against its release.

### Neutral

- Teammates on 1.3.x ignore `cortex.lock`: it is a file they do not read.
- `format` will be raised rarely: a rewording of a template keeps it.

## 6. Alternatives considered

- **A lock of every resolved agent, overlays included** — the base file and each overlay tier, with their digests. Rejected: the overlays are in git already, and the lock would change with every overlay edit. What they resolve to follows from the spec, which the lock pins, and the overlays.
- **The checksum of the spec archive instead of a digest of the files.** Rejected: a binary writes its own version from its embedded archive, built on each target with that runner's `git` and `gzip`, so the archive's bytes are not guaranteed to be the release's. The files are, and the digest of the files can be computed again from the store at any time.
- **A store-wide list of the projects, or of when each version was last used**, for pruning. Rejected: ADR-008 keeps no list of projects (§3.3), and the registry is a record of its own (#266). Last use is no proof of no use: in `store` mode a project's assistant reads the store daily without ever running `cortex sync`.
- **`cortex upgrade` rewriting a modified bootstrap file and keeping a backup**, as `cortex init --force` does. Rejected: that is the replacement #141 complained of. An upgrade rewrites only what nobody changed.
- **`cortex upgrade` upgrading the binary too.** Rejected: a project's pin and a machine's binary move at different times — one binary serves every version up to its own, and a project moves in a commit — so they are two commands. `cortex upgrade` names `cortex self-update` when its target needs it.
- **The binary left to the install scripts**, as this record first proposed. Rejected on 2026-11-04, in review: a teammate stopped by a pin newer than their binary had a line to paste into a shell, which their assistant could not run for them. And the reason it gave — on Windows the binary would replace itself while it runs — does not hold: a running executable cannot be overwritten there, but it can be renamed.
- **The assistant running `cortex upgrade` when a newer release is out.** Rejected: that moves the project's pin, a team's decision made in a commit. The assistant follows the pin; it does not move it.
- **The layout in `cortex.toml`.** Rejected: the layout belongs to the file that was written for it, and a key there would be a second source that can disagree with the file.

## 7. Follow-ups (out of scope for this ADR)

1. **The digests of the images a project depends on**, listed in #99: no project declares an image yet. The ADR that binds a project to the runtime's images extends `cortex.lock` with a `format` of its own.
2. **The runtime checking the lock**: a run that records the digest it ran with, and refuses a spec whose digest differs.
3. **The epics after this one shift by one number**: the epics numbered 010 to 022 on 2026-10-30 become 011 to 023, as ADR-002 and ADR-008 record in amendments.
4. **A registry of the machine's projects** — `~/.cortex/projects.toml`, so that `cortex prune` and `cortex upgrade --all` run from anywhere, and the cockpit (ADR-019) finds a machine's projects. Split from #48 on 2026-11-04 into a record of its own, [#266](https://github.com/xod-labs/cortex/issues/266). `cortex prune` then takes no `DIR`.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.2 the binary and its install scripts, §3.3 the store, §3.4 `cortex.toml`, §3.5 `cortex sync`, §3.7 `cortex init`, §7 the follow-ups this record takes up, §9 the version rule for later checks
- [ADR-009](ADR-009-release-cycle.md) — every merge a release, and the pace of versions that makes upgrading a routine
- [ADR-007](ADR-007-cortex-core.md) — the validator that checks overlays against a base
- Issues: [#99](https://github.com/xod-labs/cortex/issues/99), [#141](https://github.com/xod-labs/cortex/issues/141), [#266](https://github.com/xod-labs/cortex/issues/266)

## 9. Amendments

None yet.
