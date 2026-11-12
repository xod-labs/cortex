# ADR-010 — `cortex.lock`, `cortex upgrade` and the bootstrap marker

- **Status:** Implemented
- **Date:** 2026-11-03 (proposed) · 2026-11-04 (accepted, with the amendments listed in §9) · 2026-11-04 (implemented, 1.4.0)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `cli/cortex_cli/` (`sync`, `init`, `validate`, the store, three new commands `upgrade`, `prune` and `self-update`), `core/cortex_core/project.py` (the grammar of `cortex.lock`), `cli/build.py` (the build target stamped in the binary), `templates/` (the two bootstrap templates), the init parity matrix, `README.md`, `docs/getting-started.md`, `docs/extending-layers.md`, `docs/migrating-to-the-binary.md`
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) — §3.3 (the store), §3.4 (`cortex.toml`), §3.5 (`cortex sync`), §3.7 (`cortex init` and the bootstrap file) and §7, which left the lock, the upgrade and the pruning to this record; ADR-008 records it in its own amendments when this ADR is implemented · roadmap epic [#99](https://github.com/xod-labs/cortex/issues/99) · [#141](https://github.com/xod-labs/cortex/issues/141), folded in on 2026-11-03

---

## 1. Context

ADR-008 gave every project a committed `cortex.toml` that pins one released version of the spec, and every machine one store that holds each version once. It stopped there on purpose (§7): *"`cortex.lock` and upgrade tooling — a resolved manifest, `cortex upgrade`, pruning unused versions from the store — #99."*

Since then, every merge into `main` is a release (ADR-009): six releases in October, and two more on 2026-11-03 alone, each after its release candidate. Moving a project is still done by hand, and four gaps show:

- **Nothing records what a project actually ran with.** `cortex.toml` names a version, and the store holds whatever arrived under that name. A version is checked against its release's `SHA256SUMS` once, when it is downloaded, and against the table of checksums a binary embeds — which knows only the releases before that binary. Nothing a team commits says *which bytes* that version was, so nothing can tell a teammate's spec from yours, or a store altered after the download from the one the team used.
- **Moving a project to another version is four manual steps** (`docs/extending-layers.md`, *Upgrade workflow*): edit `version`, run `cortex sync`, run `cortex validate`, then read the changelog to learn what moved. Nothing says which roles, capabilities or workflows the new version adds, removes or changes, nor whether the project's overlays still find the base files they extend — until `validate` fails after the switch. And the bootstrap file stays as the old version wrote it: `cortex init` keeps an existing one unless `--force`, which replaces it, even when the team adapted it.
- **`cortex init` guesses what a kept bootstrap file is from its text** (#141). It compares the file's first line with the template's to tell the two layouts apart, and searches for `cortex.local.toml` to tell an older bootstrap from a current one. A title a team adapted — `# Cortex AI Team — Workspace Mode (Acme)` — is taken for the other layout, and every `cortex init` then suggests the `--force` that would replace the team's file. To know whether a project's bootstrap must be written again, an upgrade needs what that guess cannot give: the contract version the file was written for.
- **Following a teammate is a line to copy.** A project pinned to a version newer than a teammate's binary stops their `cortex sync`, which prints the install script's command to paste into a shell. And the bootstrap file tells the assistant to ask the user to run `cortex sync`, not to run it: after every pull that moved the pin, each step is the user's to type.

The store grows by one directory per version a machine ever synced. A version is under a megabyte, so this is tidiness rather than space, but nothing removes one, and ADR-008 deliberately keeps no list of the projects that could say which versions are still used.

## 2. Decision

**A project commits a `cortex.lock`, `cortex upgrade` moves it to another version, `cortex self-update` moves the binary, `cortex prune` removes the versions a machine's projects no longer use, and each bootstrap file says what it is in a marker — and asks the assistant to keep the machine in step with the project.**

- **`cortex.lock`** sits beside `cortex.toml` and is committed. It records the version the project resolved and the **digest of that version's spec**: one SHA-256 over every file of its three trees. `cortex sync` writes it, and checks the store against it: a spec whose files differ from what the team locked is not used. In CI, `cortex sync --locked` never writes it, and fails when it is missing or stale.
- **`cortex upgrade [VERSION]`** moves the project to another version, by default the binary's own — never down to it: a pin newer than the binary moves down only when `VERSION` names it. Before it changes anything, it says what changes — the agents added, removed and changed, layer by layer — and checks the project's overlays against the new version. It then writes `version`, syncs, and writes the bootstrap file again when nobody changed it and the new version's template differs.
- **`cortex prune DIR...`** finds the projects under the directories it is given, keeps the versions they pin and the binary's own, and removes every other version from the store. It keeps no list: the user says where the projects are.
- **Each bootstrap template carries a marker**, `<!-- cortex:bootstrap layout=single format=1 -->`, on its own line under the title, and the file `cortex init` writes from it carries the digest of what it wrote: `… format=1 digest=3f2a9c0e71b4d5a8 -->`. `layout` says which layout the file was written for, `format` which contract version, and `digest` whether anyone changed it since. `cortex init` and `cortex upgrade` read the marker, never the text around it.
- **`cortex self-update [VERSION]`** replaces the binary with a release's — the latest by default — downloaded and checked as the install script does it: against the `SHA256SUMS` of the host it comes from. Every refusal of a version newer than the binary names it, and `cortex upgrade` says when a release newer than the binary is out.
- **The bootstrap asks the assistant to keep the machine in step**: a spec missing, or of another version than `cortex.toml`'s, and it runs `cortex sync`; a binary too old for the pin, and it runs `cortex self-update`, then `cortex sync`. It says what it runs, its tool asks the user first when set to, and it never moves the project's pin of its own accord.

Out of scope: the digests of images a project depends on, which #99 listed, wait for a project to declare images (§7); the configuration cascade and the IDE modes (#98); a registry of a machine's projects, which a record of its own adds (§7); the multi-project runtime (#48); the first install of the binary, which stays the install script's.

## 3. Detailed contract

### 3.1 The bootstrap marker

Each bootstrap template carries exactly one marker line, the first line after its title and a blank line, followed by a comment that asks to keep it:

```markdown
# Cortex AI Team — Workspace Mode

<!-- cortex:bootstrap layout=workspace format=1 -->
<!-- Keep the line above as it is: `cortex init` and `cortex upgrade` read it. -->
```

The file `cortex init` writes from it carries one field more, the digest of what it wrote:

```markdown
<!-- cortex:bootstrap layout=workspace format=1 digest=3f2a9c0e71b4d5a8 -->
```

- **The form is fixed**: the whole line is `<!-- cortex:bootstrap layout=L format=N -->`, or `<!-- cortex:bootstrap layout=L format=N digest=D -->`, with `L` one of `single` or `workspace`, `N` a positive integer without leading zeros and `D` 16 lowercase hex digits. Anything else on the line, or another spelling, is no marker.
- **It is read anywhere in the file**, as a whole line: a team may add lines above it, or change the title — a Cursor rule puts its front matter first. A byte order mark before the first line hides nothing. A file with two marker lines has none — the command says so.
- **`layout`** is the layout of the template that wrote the file. It replaces the comparison of first lines.
- **`format`** is the bootstrap's contract version: it is raised when a template changes what a project's file must say for the spec to be read correctly — a new rule the LLM must follow, a renamed file it must read — and not for a rewording. It replaces the search for `cortex.local.toml`. ADR-010's templates are format 1.
- **`digest`** says whether the file is still what Cortex wrote: the first 16 hex digits of the SHA-256 of the file as written, read with LF line endings, no byte order mark, the line breaks at its end taken as one, and its marker without the digest — that is, of the template as `cortex init` writes it for that layout and theme. `cortex init` and `cortex upgrade` write it with every file they write; the templates in the spec carry none. A file whose digest still matches is *untouched*; one whose digest does not was changed by someone. It needs nothing but the file: not the version that wrote it, nor the network. It guards against no one — whoever edits the file and the digest gets the file rewritten — it only tells an untouched file from an adapted one.
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

  It depends on the files' bytes only: the same version gives the same digest whether it was downloaded, written from the binary's embedded archive or extracted on another system. A version directory missing one of the three trees has no digest: it is an error, never the digest of an empty list — and so is a file or a tree that goes while the digest is read. No path of the three trees holds a `\` or a line break, which `sha256sum` would escape: a repository test keeps it so, and the format literal.

**Why the spec only.** A lock pins what the repository does not hold. The project's overlays are its own files, already in git; recording their digests would make the lock change with every overlay edit, for nothing git does not already say. What the overlays *resolve to* is derived from the two — the spec, which the lock pins, and the overlays, which git pins.

### 3.3 `cortex sync` and the lock

After it has the pinned version in the store, `cortex sync`:

| `cortex.lock` | What sync does |
|---|---|
| missing | writes it, and says: *cortex.lock written — commit it* |
| of the pinned version, same digest | nothing |
| of the pinned version, another digest | puts the version in the store again — from the embedded archive, or downloaded and checked as in ADR-008 §3.3 — and compares again. Still different: refuses, exit 1, naming both digests, and changes nothing in the project. |
| of another version | rewrites it for the pinned version, and says: *cortex.lock now names X — commit it*. The pin moved by hand, or by `cortex upgrade` on another branch. |
| of a later format | refuses, exit 1, as a pinned version newer than the binary is refused: this cortex is older than the project's, and `cortex self-update` updates it — so the bootstrap's rule (§3.8) acts on both |
| malformed | refuses, exit 1, naming the file and what is wrong |

- **The lock is checked before anything of the project changes** — before the link or copy, `cortex.local.toml` and the lock itself — and, in `cortex init`, before its first write: a lock sync refuses leaves `cortex.toml` as it was.
- **`--locked`** never writes the lock: a missing one, or one of another version than the pin, stops sync, exit 1, changing nothing of the project, and says that `cortex sync` writes it, to be committed. A digest that differs is handled as without it. With `--from`, it exits 2. It is CI's (§3.4).
- **`--from PATH`** syncs a checkout, which has no version: the lock is neither read nor written, and sync says so.
- **The digest is computed from the store**, once per sync, a copy included: a version is under a megabyte, and the cost is that of reading it.
- **A version that leaves the store while sync reads it** — a `cortex prune` meanwhile (§3.6) — stops sync, changing nothing of the project; run again, it puts the version back. No lock is written from files sync did not read.
- **A copy follows the store**: in `copy` mode, a `cortex/` whose files are not the store's — made from a store the lock has since refused — is copied again.
- **A version put in the store again** is set aside whole first, and put back if the new one cannot be had — offline, a download refused — by this sync, or by a later one within a day when this one was cut short. After a day, the store tidies the aside away, as it does everything it set aside. Offline, the refusal says both: the store's files are not the lock's, and why they could not be had again.
- **`cortex init` writes the lock** through the sync it runs.

### 3.4 `cortex validate` and the lock

`cortex validate` reports, as warnings:

- no `cortex.lock` — *run `cortex sync`, then commit it*;
- a `cortex.lock` of another version than `cortex.toml`'s;
- a spec whose digest is not the lock's.

Only a spec of a known version is checked — the store's, a link to it, or a copy whose `.synced` names its version: a checkout synced with `--from` has no version, and no lock.

They are warnings because a later binary warns where an earlier one passed, within a major version (ADR-008 §9): a project of 1.3 has no lock, and must still validate. `--strict` fails on them.

**In CI**, `validate --strict` alone does not check the lock: it needs a synced spec, and a plain `cortex sync` before it writes a missing lock and rewrites one of another version, so neither is left to warn of. A CI installs the binary of the version `cortex.toml` pins, then runs `cortex sync --locked`, which refuses both (§3.3), then `cortex validate --strict`, which checks the digest and the overlays.

### 3.5 `cortex upgrade`

```text
cortex upgrade [VERSION] [--dry-run] [--force] [--instructions-file PATH]
```

In the project of the current directory — the nearest `cortex.toml`, as `cortex sync` finds it:

1. **The target** is `VERSION`, or the binary's own version — never below the pin: with no `VERSION`, a project pinned to a version newer than the binary is refused, exit 1, nothing changed, naming `cortex self-update` with the pinned version, and `cortex upgrade` with the binary's own to move the project down. A project moves down only when `VERSION` names the version. A version the binary does not serve is refused as by `cortex sync`: a newer one naming `cortex self-update` (§3.7), one before 1.0.0 with the versions served. A malformed one exits 2. The target is the pinned version → *already on X*: nothing moves, and what a pin moved by hand, or an upgrade cut short during its sync, left is finished. Step 5 runs: a bootstrap nobody changed whose text is not the pinned version's — another version wrote it, the pin having moved by hand, or the theme changed — is written again. A machine that reads another version, or none, and a `cortex.lock` missing or naming another version, are synced, said, and the lock named to commit; a spec synced from a checkout is left alone. The dry run says it, and changes nothing. Moving down from a pin this binary does not serve, what changes is not said: this cortex does not serve the pinned version.
2. **The target is put in the store**, downloaded and checked if need be, as by `cortex sync`.
3. **What changes is said**, comparing the two versions' trees:
   - for each layer — roles, capabilities, personalities, workflows — the files added, removed and changed, by name, the layer's `README.md` among them;
   - the templates changed, and the number of other files that changed;
   - *upgrade* or *downgrade*, and where the release notes are.
4. **The overlays are checked against the target**: the validator of ADR-007 runs with the target as the base. If it reports an error — an overlay whose `Base:` the target removed, for instance — `cortex upgrade` prints the report and stops, exit 1, having changed nothing. `--force` goes on regardless.
5. **The bootstrap files.** The instructions files are found as `cortex init` finds them — each known tool's that is there — or named by `--instructions-file`. Each one's marker is read (§3.1):
   - its digest matches — nobody changed the file → it is written again from the target's template whenever that writes another file: whatever the two formats, a rewording too, and in either direction;
   - its digest does not match — a team adapted it → it is kept. The command says so, naming `cortex init --force`, which writes it again and keeps it as `FILE.bak`, when that matters: the target's template differs from the pinned version's, or, the pinned version not in the store, the marker's format is older than the target's;
   - no digest — written before 1.4.0, or no marker → it is compared with what the pinned version's template writes for that layout and theme: the same file, it is written again when the target's template differs; another, it is kept, and said as above. The pinned version cannot be had — pruned, and offline → the file is *not checked*, and the command says so;
   - two markers → it is kept, and said; no Cortex bootstrap → nothing.

   A file written again keeps its line endings and its byte order mark, and its marker gains the digest of what was written.
6. **The switch**: what `cortex sync` would refuse — a `cortex/` that Cortex did not make — is refused first, exit 1, nothing changed. Then the bootstrap files of step 5 are written, first and each whole or not at all — a temporary file beside it, then a rename; through a link, the file it points to — then `version` in `cortex.toml`, keeping its comments, and the project is synced — `cortex.local.toml`, the link or copy, and `cortex.lock`. If anything fails, `cortex.toml`, `cortex.lock` and the bootstrap files, what the team commits, are put back as they were, and the command names them; what the sync changed on this machine, the next `cortex sync` puts right. A run killed after the files leaves files the next run reads as untouched, and finishes; one killed while it writes a file leaves the old one whole.
7. **It ends** with what to commit — `cortex.toml`, `cortex.lock`, and each bootstrap file written — and that teammates run `cortex sync` after they pull.

`--dry-run` runs steps 1 to 5 and the refusals of step 6, and changes nothing in the project; the store may gain the target version. The exit code is that of a real run: 1 when step 4 would stop it, or step 6 refuse it.

**A release newer than the binary.** With no `VERSION`, `cortex upgrade` asks the releases host which version is the latest — a few seconds at most — and, when it is newer than the binary, says so: *Cortex 1.5.0 is out — `cortex self-update`, then `cortex upgrade`, moves the project to it*. Offline, or with a `VERSION`, it says nothing of it. `CORTEX_NO_UPDATE_CHECK`, set to anything but an empty value, turns the question off — a CI, or a network that drops what it does not allow rather than refusing it. It never moves the binary itself.

### 3.6 `cortex prune`

```text
cortex prune DIR... [--keep VERSION]... [--dry-run]
```

- **It finds the projects under each `DIR`**: every `cortex.toml` up to six directories down, through links to directories too, each project once, skipping `.git`, `node_modules`, `vendor`, `.venv` and the `cortex/` a link or copy sync made. The walk is breadth first: each directory is reached by its shortest path, so a link deeper down cuts none of it off. A directory or a `cortex.toml` it cannot read is named; a project whose files hold a key this binary does not know still gives the version it pins and the one it reads now.
- **It keeps** the versions those projects pin, and the ones they read now — a teammate's bump pulled and not synced yet leaves `spec` on the version before it — the binary's own version, and each `--keep`. It removes every other version from the store, and lists both: the versions kept, each with the projects that pin it, and the versions removed.
- **No `DIR`** exits 2: there is no list of projects to fall back on (ADR-008 §3.3), and pruning without one would remove the versions of every project it was not shown.
- **The store is listed before the projects are read**: a version a project syncs meanwhile is in no list, and stays. A version that cannot be removed is named, and the others are still removed.
- **A version is removed by a rename first** — out of `versions/`, into a staging the store already cleans — then deleted, so that a sync running at the same time finds it whole or absent, and refuses rather than lock what it did not read (§3.3). A deletion cut short is said, exit 1: what is left is out of the store, and a later sync deletes it. A project whose version was removed gets it back at its next `cortex sync`, checked against its `cortex.lock`.
- **`--dry-run`** lists, and removes nothing.
- The runtime reads the store too (ADR-008 §3.6). A version only the runtime uses is kept with `--keep`.

### 3.7 `cortex self-update`

```text
cortex self-update [VERSION] [--dry-run]
```

1. **The target** is `VERSION`, or the latest release — the version its `latest/download/SHA256SUMS` redirects to; where the releases are served without that redirect, or without `latest/` at all, the command asks for a `VERSION`. A malformed one exits 2. The binary's own version → *already on X*, and nothing is downloaded. A latest release older than the binary — a pre-release running, a patch of an older line published since — is said, and installed only when named. An older one named is *a downgrade*: it is installed, and the command says that a project pinned to a later version is then refused — and, before 1.4.0, that the binary it installs has no `self-update`; on Windows, that the binary renamed aside stays, since that version does not remove it.
2. **The binary replaced is the one running**, under whichever name it was installed — `cortex` or `cortex-ai`. A source checkout runs no binary: it is refused, with the install script's command. So is a directory this user cannot write in — a binary an administrator or a package manager installed — found before the download, by an empty file made there and removed. The file it runs from is replaced where it is: a directory named after a version, `/opt/cortex-1.4.0/`, then holds another version under that name.
3. **The download** is the release asset of the running binary's own target — `cortex-linux-x86_64.tar.gz`, `cortex-linux-aarch64.tar.gz`, `cortex-macos-arm64.tar.gz` or `cortex-windows-x86_64.zip` — stamped in the binary when it is built, not read from the machine: Windows on Arm runs the x86_64 build, as the install script installs it there. It comes from where the store and the install scripts download, `CORTEX_RELEASES_URL` included, checked against the release's `SHA256SUMS`. A mismatch, or an asset that holds no binary or cannot be read, is refused, and nothing is replaced.
4. **The new binary runs before it is installed**: written beside the old one, on the same file system, once it is downloaded and checked, it must answer `--version` with the target, within two minutes. Otherwise it is removed, and the old one stays. It starts as a new process would — without what PyInstaller's bootloader set for the running one, `PYINSTALLER_RESET_ENVIRONMENT` set, as PyInstaller documents for one frozen program starting another — and writes its answer to a file, not a pipe, which the process a onefile binary starts on Windows would hold past the time limit.
5. **The replacement is a rename.** On Linux and macOS the new binary is renamed over the old one; the running process keeps the file it started from. Windows refuses to overwrite a running executable, not to rename it: the old one is renamed `NAME.old`, the new one takes its name, and the next `cortex` that starts removes the old one. A failure between the two renames puts the old one back. Nothing is imported after the rename: a PyInstaller binary reads its own file again, by its path, for a module it has not loaded yet. Every start of a binary also removes what a self-update cut short left beside it — a `.NAME.new-*` once an hour old, and on Windows `NAME.old` and `NAME.old-PID-NS`, in any case — and no other name.
6. **It ends** with the version installed, and what it did not do: no project moved — `cortex upgrade` moves one.

`--dry-run` resolves the target, downloads it, checks it and runs it, and replaces nothing.

**What the check proves.** `SHA256SUMS` comes from the host the asset comes from: it proves the download whole, not who built it, as ADR-008 records (§9, *Integrity is not origin*). The origin is HTTPS to that host: GitHub's releases, unless `CORTEX_RELEASES_URL` names another, and then `cortex self-update` says so in a note before it downloads. The bootstraps still have the assistant run it on its own there (§3.8): the same host serves the spec, which the assistant reads as its instructions after `cortex sync`, so holding the binary back alone would protect nothing. Checking where a release comes from — its attestation, or a signature of `SHA256SUMS` — is for the binary and the spec together: #275 (§7).

**Where it is named.** Every refusal of a version newer than the binary — a pinned version in `cortex sync` and `cortex init`, a target of `cortex upgrade`, a `cortex.lock` of a later format — names `cortex self-update` instead of the install script's command. A binary before 1.4.0 still names the install script, which installs the latest release: the first move to a binary with `self-update` is the install script's.

### 3.8 The bootstrap keeps the machine in step

The first rules of both bootstrap templates — what the assistant does before anything else — become actions it takes, where they asked the user to act:

| The assistant finds | It |
|---|---|
| no `cortex.local.toml`, or no `spec` in it | runs `cortex sync` at the project root — the workspace root in workspace mode |
| a spec of another version than `cortex.toml`'s | runs `cortex sync`, and goes no further until it succeeded |
| `cortex sync` saying this cortex is too old for the project — a pinned version, or a `cortex.lock` format, newer than it | runs the command the refusal names — `cortex self-update X`, or, from a binary before 1.4.0, the install script's — then `cortex sync` again |
| no `cortex` command, nor `cortex-ai`, on `PATH` or in `~/.cortex/bin` | tells the user how to install it, and goes no further |

- **The command it runs** is `cortex-ai` when there is one — the install scripts name it so when another program called `cortex` comes first — and otherwise `cortex`, once `cortex --version` printed `cortex` followed by a version: anything else is another program, whose `sync` it must not run. It is looked up only when a rule has the assistant run one, not at every conversation, and in `~/.cortex/bin` (`%USERPROFILE%\.cortex\bin` on Windows) too when the tool's `PATH` has neither — an editor started before the install changed `PATH`.
- **It says what it runs, and why, before it runs it.** Whether the user approves each command is the setting of the assistant's tool, not the bootstrap's: a tool set to ask, asks. An assistant that cannot run commands asks the user to run them.
- **It never moves the project's pin of its own accord**: `cortex upgrade`, or an edit of `version`, is the team's decision, made in a commit. Asked by the user, it does — `cortex upgrade --dry-run` first, to show what changes. In a workspace whose `cortex.toml` is not committed, the pin is the developer's own, and so is the decision.
- **It goes no further until the sync succeeded**: it does not work from a spec of another version than the one the project pins.

These rules are part of format 1 (§3.1): no released template carried a marker before them.

### 3.9 What stays as it is

- `cortex.toml` and `cortex.local.toml`: no new key. The layout is read from the bootstrap marker, not stored.
- The store's layout: a version directory still holds its three trees and nothing else. Nothing is per project, and nothing lists the projects.
- `cortex upgrade` moves a project, never the binary; `cortex self-update` moves the binary, never a project. The install script stays the way to install the binary the first time.
- The runtime does not read `cortex.lock` (§7).

## 4. Phases

Execution order: **1 → 2 → 3 → 4 → 5**. All five ship in one release, 1.4.0: new commands and a new committed file, nothing a project must change. Two release candidates came first, `1.4.0-rc.1` and `1.4.0-rc.2`, cut from the release branch, and `cortex self-update` ran from one to the other on the four targets.

### Phase 1 — The bootstrap marker

The marker in both templates; `cortex init` reads a kept file's layout and format from it (§3.1); the init parity matrix re-captured.

Acceptance criteria:

- both bootstrap templates carry exactly one marker line, the third, with their layout and `format=1`, and the comment that asks to keep it, the fourth
- the init parity matrix, re-captured, differs from the previous one by three lines in every bootstrap file it holds — a blank line, the marker and its comment — and in nothing else
- a marker is found below a Cursor rule's front matter, and after a byte order mark
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
- a lock with an unknown key, a malformed digest or `format = 2` is refused, naming the file and the key; `cortex init` refuses it before writing anything
- `cortex sync --locked` on a project with no lock, or a lock of another version, exits 1 and leaves the lock as it was; with a lock that matches, it exits 0
- a version missing one of its trees has no digest, and no lock is written from it
- `cortex validate` warns on a missing lock, a lock of another version and a digest that differs, and `--strict` fails on each; a project of 1.3 with no lock still validates without `--strict`

### Phase 3 — `cortex upgrade`

The command of §3.5.

Acceptance criteria:

- on a project pinned to an older version, `cortex upgrade` moves it to the binary's version: `version` is written with the file's comments kept, the spec synced and the lock rewritten; the output names the agents added, removed and changed, by layer
- `cortex upgrade X` to an older version says *downgrade*, and moves it; to the pinned version says *already on X* and moves nothing
- with no `VERSION`, a project pinned to a version newer than the binary is refused, exit 1, the project unchanged
- a version newer than the binary is refused, naming `cortex self-update` (§3.7); a malformed one exits 2
- `--dry-run` leaves every file of the project byte for byte, and exits as the real run would
- an overlay whose `Base:` the target removed stops the upgrade, exit 1, with the validator's report and the project unchanged; `--force` moves it anyway
- `cortex init` writes the bootstrap's marker with its digest; a bootstrap whose digest matches is written again whenever the target's template differs, in either direction; one a team changed is kept, and the command names `cortex init --force`; one without a digest is compared with the pinned version's template, and said *not checked* when that version cannot be had
- on the pinned version, a bootstrap nobody changed that another version wrote is written again
- a sync that fails after `version` was written leaves `cortex.toml`, `cortex.lock` and the bootstrap files as they were; a run killed during the sync leaves the bootstraps written, and the next run, on the pinned version, syncs the lock and the machine
- each bootstrap file is written whole or not at all

### Phase 4 — `cortex prune`

The command of §3.6.

Acceptance criteria:

- with projects under `DIR` pinned to A and B, and a store holding A, B, C and the binary's own version, `cortex prune DIR` removes C only, and lists A and B with their projects
- `--keep C` keeps C; `--dry-run` removes nothing; the binary's own version is never removed
- no `DIR` exits 2; a `cortex.toml` inside `node_modules/` or a synced `cortex/` is not counted; one that cannot be read is named
- a project six directories down is found, one seven down is not; a project reached through a deeper link first is still found by its shorter path
- a project whose version was pruned gets it back at its next `cortex sync`, and the lock still matches; a sync whose version a prune removes while it reads writes no lock

### Phase 5 — `cortex self-update`, and the bootstrap keeps the machine in step

The command of §3.7 and the refusals that name it, the notice of `cortex upgrade` (§3.5), the rules of §3.8 in both bootstrap templates; the init parity matrix re-captured.

Acceptance criteria:

- `cortex self-update` replaces the binary with the latest release's, checked against its `SHA256SUMS`; `cortex self-update X` with X's; on the binary's own version it says *already on X* and downloads nothing
- the asset is that of the target the binary was built for; a releases host other than GitHub's is said in a note
- a checksum mismatch, an asset that holds no binary, or a new binary that does not answer `--version` with the target, leaves the old binary byte for byte
- a running executable is replaced on every target — on Windows renamed aside, and the `.old` it leaves removed by the next run, with no other name
- a source checkout, and a directory the user cannot write in, are refused with the install script's command; `--dry-run` replaces nothing; a malformed version exits 2
- `cortex sync` on a project pinned to a version newer than the binary names `cortex self-update`, and so does `cortex upgrade` given such a version
- with no `VERSION`, `cortex upgrade` says when a release newer than the binary is out, says nothing of it offline, and asks nothing with `CORTEX_NO_UPDATE_CHECK` set
- both bootstrap templates have the assistant run `cortex sync`, and `cortex self-update` when sync asks for it, and `cortex upgrade` only when the user asks for it — through `cortex-ai` when there is one, and `cortex` once it says it is Cortex; the init parity matrix, re-captured, differs by those lines only

## 5. Consequences

### Positive

- **A team commits what its agents ran with**: one digest that a teammate's machine, a CI runner or an auditor recomputes from the spec they have. A spec altered after download, or a release served differently from the one locked, is refused instead of used.
- **Upgrading is one command that says what it does first**: the agents that change, the overlays that would break, the bootstrap file that must follow — before anything is written. A bootstrap file nobody changed follows every template, a rewording too, and an upgrade cut short resumes at the next run — the bootstrap, the lock and the machine with it.
- **The bootstrap file says what it is**: a team can retitle and extend it, and `cortex init` still knows its layout and format. #141 is closed by the same marker an upgrade needs.
- **The store can be emptied of old versions** without a registry, and safely: a version pruned by mistake comes back at the next sync, checked against the lock.
- **Following a teammate takes no command typed**: after a pull that moved the pin, the assistant syncs, and updates the binary when the pin asks for it — saying so, and asking first where the user's tool is set to ask. A user who runs the commands by hand has one each: `cortex self-update`, `cortex sync`.

### Negative

- **One more committed file**, and one more line in every bootstrap file, with a digest that means nothing to a reader. A project's first sync with 1.4.0 adds `cortex.lock` as an uncommitted change.
- **Every sync reads the pinned version once more**, to compute its digest: under a megabyte of reading.
- **A modified bootstrap file is never rewritten by `cortex upgrade`.** The team carries its changes over by hand after `cortex init --force`, as today.
- **A bootstrap written before 1.4.0 has no digest**, so only the pinned version's template can tell it untouched. Once its pin has moved without it — by hand, or by an upgrade that could not check it, the pinned version pruned and offline (*not checked*) — the file no longer matches that template, and `cortex upgrade` keeps it, saying nothing on the pinned version: `cortex init --force` writes it again. A file with a marker but no digest — a 1.4.0 pre-release's — is written again once to gain its digest, even when its text is the target's.
- **A run killed while it writes a bootstrap file leaves `FILE.<pid>.tmp` beside it**, as a write of `cortex.toml` killed there does: the file itself is whole.
- **`cortex prune` is only as good as the directories it is shown.** A project elsewhere loses its version, and gets it back at its next sync, which needs the network.
- **A binary that replaces itself repeats the install scripts' checks** in Python — the asset of the binary's own build target, its `SHA256SUMS` — and leaves, on Windows, a `.old` beside the new binary until the next run.
- **The assistant runs commands on its own** — `cortex sync`, `cortex self-update` — within what the user's tool allows: a tool set to run commands unasked downloads a binary unasked, checked against the `SHA256SUMS` of the host that serves it — not against who built it, until #275.

### Neutral

- Teammates on 1.3.x ignore `cortex.lock`: it is a file they do not read.
- `format` will be raised rarely: a rewording of a template keeps it, and still reaches every file nobody changed, through its digest.

## 6. Alternatives considered

- **A lock of every resolved agent, overlays included** — the base file and each overlay tier, with their digests. Rejected: the overlays are in git already, and the lock would change with every overlay edit. What they resolve to follows from the spec, which the lock pins, and the overlays.
- **The checksum of the spec archive instead of a digest of the files.** Rejected: a binary writes its own version from its embedded archive, built on each target with that runner's `git` and `gzip`, so the archive's bytes are not guaranteed to be the release's. The files are, and the digest of the files can be computed again from the store at any time.
- **A store-wide list of the projects, or of when each version was last used**, for pruning. Rejected: ADR-008 keeps no list of projects (§3.3), and the registry is a record of its own (#266). Last use is no proof of no use: in `store` mode a project's assistant reads the store daily without ever running `cortex sync`.
- **`cortex upgrade` rewriting a modified bootstrap file and keeping a backup**, as `cortex init --force` does. Rejected: that is the replacement #141 complained of. An upgrade rewrites only what nobody changed.
- **`cortex upgrade` upgrading the binary too.** Rejected: a project's pin and a machine's binary move at different times — one binary serves every version up to its own, and a project moves in a commit — so they are two commands. `cortex upgrade` names `cortex self-update` when its target needs it.
- **The binary left to the install scripts**, as this record first proposed. Rejected on 2026-11-04, in review: a teammate stopped by a pin newer than their binary had a line to paste into a shell, which their assistant could not run for them. And the reason it gave — on Windows the binary would replace itself while it runs — does not hold: a running executable cannot be overwritten there, but it can be renamed.
- **The assistant running `cortex upgrade` when a newer release is out.** Rejected: that moves the project's pin, a team's decision made in a commit. The assistant follows the pin; it does not move it.
- **The layout in `cortex.toml`.** Rejected: the layout belongs to the file that was written for it, and a key there would be a second source that can disagree with the file.
- **A bootstrap file judged against the pinned version's template only**, as this record first proposed, rewritten when the target's format is newer. Rejected on 2026-11-04, in the counter-review: a rewording never reached a file nobody changed; and once the pin had moved — by hand, or by an upgrade cut short after the switch — the pinned version was no longer the one that wrote the file, which then looked adapted for good.
- **The version that wrote the file, in its marker** — `version=1.4.0` — compared with that version's template. Rejected on 2026-11-04: it needs that version's templates, which a store pruned and offline does not have. The digest needs nothing but the file.
- **A CI that checks the lock with `git status` after a plain `cortex sync`.** Rejected on 2026-11-04: each team would write it, and `git diff`, the obvious one, does not see a lock that was never committed. `cortex sync --locked` is the idiom of `cargo --locked` and `uv sync --locked`.
- **The assistant never running `cortex self-update` on its own when `CORTEX_RELEASES_URL` names another host.** Rejected on 2026-11-04: that host serves the spec too, which the assistant reads as its instructions; holding the binary back alone protects nothing. Checking origin is #275, for both.

## 7. Follow-ups (out of scope for this ADR)

1. **The digests of the images a project depends on**, listed in #99: no project declares an image yet. The ADR that binds a project to the runtime's images extends `cortex.lock` with a `format` of its own.
2. **The runtime checking the lock**: a run that records the digest it ran with, and refuses a spec whose digest differs.
3. **The epics after this one shift by one number**: the epics numbered 010 to 022 on 2026-10-30 become 011 to 023, as ADR-002 and ADR-008 record in amendments.
4. **A registry of the machine's projects** — `~/.cortex/projects.toml`, so that `cortex prune` and `cortex upgrade --all` run from anywhere, and the cockpit (#46) finds a machine's projects. Split from #48 on 2026-11-04 into a record of its own, [#266](https://github.com/xod-labs/cortex/issues/266). `cortex prune` then takes no `DIR`.
5. **Where a release comes from**: `cortex self-update`, `cortex sync` and the install scripts check a download against the `SHA256SUMS` of the host that serves it — integrity, not origin (§3.7). Checking the release's attestation, or a signature of `SHA256SUMS` that the binary checks with a key it embeds, for the binary and the spec together — [#275](https://github.com/xod-labs/cortex/issues/275), raised by the counter-review of 2026-11-04.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.2 the binary and its install scripts, §3.3 the store, §3.4 `cortex.toml`, §3.5 `cortex sync`, §3.7 `cortex init`, §7 the follow-ups this record takes up, §9 the version rule for later checks
- [ADR-009](ADR-009-release-cycle.md) — every merge a release, and the pace of versions that makes upgrading a routine
- [ADR-007](ADR-007-cortex-core.md) — the validator that checks overlays against a base
- Issues: [#99](https://github.com/xod-labs/cortex/issues/99), [#141](https://github.com/xod-labs/cortex/issues/141), [#266](https://github.com/xod-labs/cortex/issues/266), [#275](https://github.com/xod-labs/cortex/issues/275)

## 9. Amendments

The record was still *Proposed* when these were made: the body above already says each. They are listed against the text the maintainer reviewed on 2026-11-04.

### While the phases were implemented

- **A copy follows the store, and a version put in the store again is set aside first** (§3.3). A `cortex/` copied from a store the lock has since refused is copied again; a version fetched again is set aside first, and put back when the new one cannot be had — offline, say.
- **Only a spec of a known version is checked by `cortex validate`** (§3.4): a checkout synced with `--from` has no version, and no lock to compare.
- **A sync that fails puts back the lock too**, not only `version` (§3.5, step 6): both are what the team commits.
- **What `cortex prune` keeps, and the order it works in** (§3.6): links to directories followed, each project once; the version a project reads now kept beside the one it pins; the store listed before the projects are read; a version that cannot be removed does not stop the others.
- **A latest release older than the binary is installed only when named, and the new binary starts in a clean environment** (§3.7, steps 1 and 4): `PYINSTALLER_RESET_ENVIRONMENT`, as PyInstaller documents for one frozen program starting another.
- **The assistant runs `cortex upgrade` when the user asks**, the dry run first, and never of its own accord; `CORTEX_NO_UPDATE_CHECK` turns off the question `cortex upgrade` asks of the releases (§3.5, §3.8). The maintainer's decisions of 2026-11-04.

### The counter-review of 2026-11-04

Bluspark's counter-review of the five phases, arbitrated by the maintainer the same day.

- **The marker carries the digest of what Cortex wrote** (§3.1, §3.5 step 5). Judged against the pinned version's template, and only when the format rose, a bootstrap nobody changed never got a rewording, and looked adapted for good once the pin had moved by hand or an upgrade was cut short after the switch. The digest needs nothing but the file: an untouched file follows the target's template, whatever the formats; on the pinned version, `cortex upgrade` finishes what an upgrade cut short left. The maintainer chose it over the version that wrote the file, which needs that version's templates (§6).
- **`cortex upgrade` moves no pin down unless the version is named** (§3.5 step 1): with no `VERSION`, a binary older than the pin moved the team's pin down to its own version, and exited 0.
- **`cortex sync --locked`, for CI** (§3.3, §3.4): `validate --strict` after a plain sync never saw a missing or stale lock, which that sync had just written.
- **What `cortex self-update`'s check proves** (§3.7): integrity against the host it downloads from, which a note names when it is not GitHub's; origin is #275, for the binary and the spec together (§7).
- **The asset of the binary's own build** (§3.7 step 3): Python reports the native processor of Windows on Arm inside the x86_64 build the install script puts there, and self-update refused it.
- **The command's name, in the bootstraps** (§3.8): `cortex-ai` when there is one, `cortex` once it says it is Cortex — the assistant had no way to tell which name a machine installed, and might have run another program's `sync`.
- **A lock is never written from a version missing its files** (§3.2, §3.3, §3.6): the digest of a missing tree was the empty set's, and a `cortex prune` between sync's two reads locked it; every sync after refused the project. A sync whose version leaves the store now stops, nothing changed.
- **`cortex init` checks the lock before its first write** (§3.3): it wrote `cortex.toml`, then refused the lock, and said nothing had changed.
- **Smaller ones**: a byte order mark hides no marker (§3.1); a version set aside by a sync cut short is put back by the next, offline (§3.3); `prune` walks breadth first, keeps both versions of a project whose files it cannot fully read, and says where a removal cut short left a version (§3.6); `upgrade --dry-run` exits as the run would, and a layer's README is listed (§3.5); self-update's `--version` answer goes to a file, a corrupt zip is refused rather than a traceback, and every start removes what a self-update cut short left (§3.7).

### The second round of the counter-review, 2026-11-04

Bluspark's second round, bounded to what the first changed. Most findings were tests a mutation showed missing; these changed behaviour or text:

- **An upgrade cut short resumes whole** (§3.5, steps 1 and 6): on the pinned version, a `cortex.lock` missing or naming another version, and a machine that reads another version, are synced. The bootstrap files are written first, each through a temporary file and a rename.
- **Moving down from a pin this binary does not serve** no longer advises `cortex self-update` to that pin (§3.5, step 1).
- **A file or a tree that goes while the digest is read is an error** (§3.2): the walk had skipped it, and given a partial digest.
- **What a sync set aside is put back within a day** (§3.3): after that, the store tidies it as it does everything it set aside. Offline, the refusal also says the store's files are not the lock's.
- **A `cortex.lock` of a later format is refused as a newer pin is** (§3.3, §3.8): this cortex is older than the project's, and the bootstrap's one rule acts on both.
- **The command is looked up when a rule needs it** (§3.8), not at every conversation, in `~/.cortex/bin` too, and accepts `cortex` followed by any version, a pre-release's included. The rule for a spec of another version says to go no further until the sync succeeded.
- **Self-update** (§3.7): the new binary's file is made once it is downloaded, so a long download loses it to no other start's tidying; a start removes only the names a self-update gives; a Windows downgrade before 1.4.0 says the old binary stays; a directory named after a version is said to get another version.
- **The CI lines of the docs** keep `--locked` for projects pinned to 1.4.0 or later: 1.3 refuses the option.

### After 1.4.0 — the text corrected (2026-11-04)

Bluspark's last notes on the text, read after the release; none changes what was shipped:

- **The status says what was accepted**: the text as phase 5 left it, with the amendments listed above.
- **§1**: the releases of October and of 2026-11-03 are counted without their release candidates — six, then two.
- **§3.5, step 1, and §5**: since the bootstrap files are written before `version` (step 6), an upgrade cut short no longer moves the pin without them. What leaves a bootstrap behind its pin is a pin moved by hand, or an upgrade that could not check the file, the pinned version pruned and offline. An upgrade cut short during its sync still leaves the lock and the machine to finish.
- **§3.5**: the release check asks the releases host which version is the latest.
- **§4**: the two release candidates, and the self-update from one to the other on the four targets, arbitration 19 of #242.
- **§4, phase 3**: a version newer than the binary is refused naming `cortex self-update`, not "the upgrade command".
- **§5**: the asset self-update downloads is that of the binary's own build target (§3.7, step 3).
- **The header**: `cli/build.py` is affected, since it stamps the build target in the binary.
- **The planned records this one named by number** — the multi-project runtime, ADR-021 (§2), and the cockpit, ADR-019 (§7) — have no number since 2026-11-04: a planned record takes one only once its text is written. They are #48 and #46, and the text names them so. The project registry this record leaves to a record of its own (§7.4) is ADR-011.

### 2026-11-07 — The layout without `--workspace`, and a root that is no work tree

- **§3.1** ([#329](https://github.com/xod-labs/cortex/issues/329)). Without `--workspace`, `cortex init` takes the layout the instructions file was written for — by its markers when they agree, by its title otherwise, found below blank lines and comments a team wrote above it too — never below a line of text, an example in a team's notes, a file whose layout the note then says cannot be told — as every rule reads it, rule 1 included — and writes that layout's template, saying so under `--force`: a workspace's file was written over with a single project's. Under `--force` without `--workspace`, a file whose title sits below a line of text — an older bootstrap with its personality block too, whose layout the note without `--force` says cannot be told — is written as a single project's, and the note says so, naming `cortex init --tool X --workspace --force` for a workspace's; a title inside a comment is no title. Rule 3 is no longer said of that file; said of a single project's file under `--workspace` — or of one written before the marker, which rule 2 says first — it names `cortex init --tool X --workspace --force`, as `--force` alone keeps the file's layout; of another tool's file of the other layout, it names `--tool X --force` — with `--workspace` when this run writes a workspace's and the file was not written for one, by no marker, several or none — which keeps the file as .bak and its team's part: never to remove the file, whose team's part a root in no repository would lose; of one `--force` would write as it is — a workspace's up to date as one — it names no command. Every command a note names carries the tool, `--tool X` — a custom file with its `--instructions-file` — as `cortex upgrade`'s note does: `--force` alone is refused where several instructions files are, and a project left with none writes Copilot's; of a workspace's file `--force` keeps as one, the note names `cortex init --tool X` to run once it is removed, and the backup to copy the team's part back from — `.bak`, or the `.bak.N` written. `cortex upgrade`'s note names `cortex init --tool X --force` for a workspace's file too, without `--workspace`, which would also write the team's pair in `agents/`. Markers that agree say the layout, though several are still none for the other rules.
- **§3.3 and §3.5, step 7**. At a project root with no `.git` at it or above it — a 2.B workspace, committed nowhere (ADR-008 §9) — `cortex sync` writes the lock without *commit it* — nor do `cortex sync --locked` and `cortex validate` say it — and `cortex upgrade` ends with the files it wrote, committed nowhere; one linked into a repository is to commit where it is. `cortex upgrade --all` says *nothing — in no git work tree* of such a project. The same test tells whether there is a repository to ignore files in: where git cannot answer — not on the PATH, or refusing the repository — a project at its root or in a subdirectory of it reads its own `.gitignore` for a line that names the path as git reads it — split at line feeds only, one carriage return, the byte order mark at the start and trailing spaces set aside, no negation below it that can match the path, and no `.gitignore` that is a symbolic link, which git does not follow, and which nothing is written through: the note names the link — and `cortex init` writes that file there. Without such a line, the note says git could not be asked, never that git does not ignore the file: a pattern, a negation that can match it — its pattern read as git reads it, case and a decomposed accent aside, or one Cortex cannot read, a `[:alpha:]` class — a `.gitignore` of a folder above, `info/exclude` and `core.excludesFile` are git's to read.
