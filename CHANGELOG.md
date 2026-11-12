# Changelog

All notable changes to Cortex are recorded here — **this file is the single source of truth for versions**.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Each entry links to its detailed, narrated
release note under [`changelog/`](changelog/).

## [Unreleased]

### Changed
- **`cortex init --force` keeps a workspace's layout without `--workspace`** ([#329](https://github.com/xod-labs/cortex/issues/329)). The template was chosen by the option alone, and a workspace's bootstrap was written over with a single project's — no services, no `@alias`, no workspace tiers — in silence. Without the option, the template is the layout the file says it was written for — by its markers when they agree, by its title otherwise, below blank lines and comments a team wrote above it too — never below a line of text, a file whose layout the note then says cannot be told — as `cortex upgrade` reads it, and whether a file is Cortex's at all — and a note says so; the services and the team's pair in `agents/` stay the option's. `--workspace` still makes a single project's file a workspace's — kept without `--force`, the note names `cortex init --tool X --workspace --force`, as `--force` alone keeps the file's layout — and removing the file first writes a single project's: the note `--force` gives a workspace's file names `cortex init --tool X` to run once it is removed, and the backup to copy the team's part back from — `.bak`, or the `.bak.N` written. `cortex upgrade`'s note names `cortex init --tool X --force` for a workspace's file, without `--workspace`, which would also write the team's pair in `agents/`. Every note of `cortex init` and `cortex upgrade` that names `cortex init` names the tool — `--tool X`, a custom file with its `--instructions-file` — so that the command writes that file: without it, `--force` is refused where several instructions files are, and a project left with none writes Copilot's. `cortex init` without `--force` no longer says a workspace's file was written for another layout, and of another tool's file of the other layout it names `--tool X --force` — with `--workspace` when this run writes a workspace's and the file was not written for one, by no marker, several or none — which keeps the file as .bak and keeps its team's part — never to remove it — and names nothing of one `--force` would write as it is, a workspace's up to date as one. Under `--force` without `--workspace`, a file whose title sits below a line of text — an older bootstrap with its personality block too, whose layout the note without `--force` says cannot be told — is written as a single project's, and the note says so, naming `cortex init --tool X --workspace --force` for a workspace's; a title inside a comment is no title.
- **The workspace template's step 4.2 lists the `@alias` values where the services are declared** ([#329](https://github.com/xod-labs/cortex/issues/329)): the table below the end marker, or the services' `project-overview.md` without one — no longer those files alone. `cortex upgrade` writes the step again in every block nobody changed. ADR-001 §3.5 records where the Prompt Manager finds the table since ADR-012.

### Fixed
- **`cortex init` takes the link Cortex made for its own once `cortex.toml` is gone** ([#328](https://github.com/xod-labs/cortex/issues/328)). It judged `cortex/` against a project with no spec, and called a link `cortex sync --link --from` had made one Cortex did not make. It reads the `spec` `cortex.local.toml` still names, as `cortex sync` does; a link that leads nowhere is replaced by `--link --from`, as #325 has it, and refused otherwise naming `cortex init --link --from` — no sync runs without the pin.
- **`cortex sync` and `cortex upgrade` no longer say to commit at a root in no git repository** — a 2.B workspace, where `cortex init` says the files are committed nowhere. The lock is *written*, without *commit it* — nor do `cortex sync --locked` and `cortex validate` say it; `cortex upgrade` names the files it wrote as committed nowhere, each developer moving their own (ADR-008 §9), and a file linked into a repository as to commit where it is; `cortex upgrade --all` says *nothing — in no git work tree* of such a project. A repository is a `.git` at the root or above it, the root as written or resolved — the same test for `cortex init`'s note, git or not on the machine. Where git cannot answer for one — not on the PATH, or refusing the repository — at its root or in a subdirectory of it, the project's own `.gitignore` is read for a line that names the path as git reads it — split at line feeds only, one carriage return, the byte order mark at the start and trailing spaces set aside, no negation below it that can match the path, and no `.gitignore` that is a symbolic link, which git does not follow, and which nothing is written through: the note names the link — and `cortex init` writes it in a subdirectory too — it wrote none there, and said nothing. Without such a line, the note says git could not be asked, never that git does not ignore the file: a pattern, a negation that can match it — its pattern read as git reads it, case and a decomposed accent aside, or one Cortex cannot read, a `[:alpha:]` class — a `.gitignore` of a folder above, `info/exclude` and `core.excludesFile` are git's to read.

## [1.7.0] - 2026-11-06 — The Restaurant at the End of the Universe _(Released)_
[Full notes](changelog/1.7.0.md)

### Changed
- **The bootstrap file is in two parts** ([ADR-012](docs/adr/ADR-012-bootstrap-block.md) §3.1–§3.5, [#317](https://github.com/xod-labs/cortex/issues/317)). Format 2 of the templates closes Cortex's text with `<!-- cortex:bootstrap end -->`; below it, the file is the team's. `cortex init --force` and `cortex upgrade` write Cortex's block again and put what follows back byte for byte, the block taking the file's line endings and byte order mark. The digest in the marker covers Cortex's block only, so a team's part no longer keeps the file from following Cortex. A format 1 file nobody changed becomes format 2 at the next upgrade; one a team changed is kept, with a note — `cortex init --force` writes format 2, and the team copies its rules from the `.bak` below the end marker. The workspace template no longer holds a table of services to fill in Cortex's block: the table goes below the end marker. The docs say where a rule of one's machine and a team convention go instead. Nothing is written that would lose a team's part: a file that has one is kept, with a note, when the target writes a bootstrap without an end marker — a move down to a version before 1.7.0, where `cortex init --force` writes the older file whole and the `.bak` keeps the team's part — and a line break of any number of carriage returns is one to the digest and to the split alike.
- **`cortex upgrade` says where a team's rules go** ([#318](https://github.com/xod-labs/cortex/issues/318)). The note on a bootstrap a team changed adds that its own rules go below the end marker, where Cortex keeps them — when the file `cortex init --force` writes has one — and names `--workspace` for a workspace's file, told by its marker or, without one, by its title; a file written again with a team's part says that what is below its end marker stays as it is, the dry run too.
- **`cortex sync --link --from` replaces a link that leads nowhere** ([#310](https://github.com/xod-labs/cortex/issues/310)). A `cortex/` link the project reads, to a checkout moved or deleted since, was refused until it was removed by hand — though naming the checkout's new path says where it should lead. A link sync from a checkout now replaces it, and says so — `cortex init --from` as well, in link mode ([#325](https://github.com/xod-labs/cortex/issues/325)); a case it refuses, it refuses before it writes anything. A plain `cortex sync`, a copy or a store sync still refuse it; a live link Cortex did not make, a link the project does not read and anything that is not a link are never replaced. The instructions file's rule says the same.

### Fixed
- **`cortex upgrade` passed over two things in silence** ([#313](https://github.com/xod-labs/cortex/issues/313)). On the pinned version, a bootstrap with no marker that is not that version's file — an older Cortex's, or one a team changed — is now said, as it is when the version moves. And `--commit` no longer ends on "Commit cortex.lock." after finding the lock as committed already.
- **`cortex upgrade --all` no longer lists a project as committed already under what to commit** ([#326](https://github.com/xod-labs/cortex/issues/326)). Its summary says the files are as committed already, with nothing to commit, as it does of files git ignores; a file the run wrote outside the work tree is still named, to commit where it is.
- **`testing/component-testing` no longer says component axe catches contrast** ([#311](https://github.com/xod-labs/cortex/issues/311)). jsdom and happy-dom compute no colors, and jest-axe turns the rule off: the component rung checks names, roles, labels and ARIA states, and contrast needs a real browser, as `testing/accessibility-testing` says.
- **Six role cards lost an emoji to a replacement character** ([#314](https://github.com/xod-labs/cortex/issues/314)): `architect`, `consultant-platform`, `lead-backend`, `lead-frontend`, `qa-automation` and `prompt-manager` showed a replacement character in their "Anti-patterns" heading and before the next one. The 🚫 is back, and `repo checks` now refuses a tracked file that holds U+FFFD.
- **`cortex sync` no longer names a checkout where a store moved** ([#312](https://github.com/xod-labs/cortex/issues/312)). A `cortex/` link the project reads that leads nowhere was always called *a checkout that is gone*, though a link into a store moved since — `CORTEX_HOME` changed, the old one deleted — leads nowhere too. One message now says both: *a link to X, which leads nowhere — a checkout or a store moved or deleted since it was linked*, then `cortex sync --link --from` the checkout it should follow, which replaces it, or `cortex sync` once it is removed.

## [1.6.0] - 2026-11-06 — We Apologise for the Inconvenience _(Released)_
[Full notes](changelog/1.6.0.md)

### Added
- **The accessibility specialist** (`engineering/accessibility-specialist`), cast as Prak in h2g2 and Flynn in breaking-bad. The role audits against WCAG 2.2 or the standard a law imposes on the project, reviews designs before they are built, and never states a conformance level from a tool's results.
- **Two capabilities for accessible interfaces.** `practices/web-accessibility` is what accessible UI is made of, WCAG 2.2 AA included. `testing/accessibility-testing` is which check runs where: lint, HTML validation and component axe on the pull request, browser scans on a schedule, behind a baseline that only shrinks.
- **`design/open-design`**, the first `design/` capability: mockups rendered from a versioned design system — a package of `manifest.json`, `DESIGN.md` and `tokens.css` — held to accessibility acceptance criteria, and handed off as a component map rather than as markup.
- **Two workflows.** `engineering/accessibility-audit` runs an audit from its frame to the statement, the regression harness and the recurring cadence. `product/target-mockup` takes a redesign from the brief to a hand-off the team can build.

### Changed
- **The Lead Frontend aims at WCAG 2.2 AA**, WCAG 2.1 AA before. It loads `practices/web-accessibility`, and `testing/accessibility-testing` when a task wires accessibility checks; QA automation loads the latter for a project with a user interface. `frontend-testing` keeps browser accessibility scans off the required path of pull requests, and takes the requests to wire accessibility checks into tests or CI; an audit goes to `engineering/accessibility-audit`.

### Fixed
- **`cortex sync` names a link to a checkout that is gone for what it is** ([#303](https://github.com/xod-labs/cortex/issues/303)). A `cortex/` that `cortex sync --link --from` linked to a checkout since moved or deleted was refused as a link Cortex did not make, by every later sync. The refusal now says the checkout is gone, and what to do: remove the link, then `cortex sync --link --from` the checkout's new path, or `cortex sync` for the pinned version.
- **`cortex upgrade --commit` and a file outside the project's git work tree** ([#304](https://github.com/xod-labs/cortex/issues/304)). A bootstrap file the upgrade writes outside the work tree — linked to a shared file, or named by `--instructions-file` — is named where it is, the link that leads there too, and stays in what the project must commit, to commit where it is: it used to drop out of the summary, and the note named the link, or a path such as `alpha/../x.md`. The work tree's top is found by what it is, not by how its path is spelt: on macOS, a project's path typed in another case put every file outside the work tree, and nothing was committed — or, for a project in a directory below the top, hid from `--commit` a change of yours to `cortex.toml`, which it could then commit.
- **The bootstrap rule for a spec that is gone keeps link mode** ([#304](https://github.com/xod-labs/cortex/issues/304)): a `cortex/` linked to a checkout since moved is synced again with `cortex sync --link --from`: `--from` alone, unless `cortex.toml` sets `sync = "link"`, read the checkout in place, with no link.
- **`cortex init --tool cursor` writes a rule Cursor applies in every conversation** ([#306](https://github.com/xod-labs/cortex/issues/306)). `.cursor/rules/cortex.mdc` had no frontmatter. Cursor's documentation applies a rule with `alwaysApply: false`, no `description` and no `globs` only where a chat @-mentions it, and one with no frontmatter is inferred to be read the same — a real conversation is to confirm it ([#137](https://github.com/xod-labs/cortex/issues/137)): a new conversation would not have read the bootstrap. The file now opens on `alwaysApply: true`, and the digest in its marker covers it. `cortex upgrade` writes it again in a rule nobody changed, and says so of one a team changed: `cortex init --force` writes it.

## [1.5.0] - 2026-11-05 — Mind-Bogglingly Big _(Released)_
[Full notes](changelog/1.5.0.md)

### Added
- **The project registry, and `cortex project`** (ADR-011 §3.1–§3.3). `projects.toml`, beside the store — `~/.cortex/projects.toml`, or `$CORTEX_HOME`'s — lists a machine's Cortex projects, one entry per root of `cortex.toml`: a name and a path, nothing else. `cortex project add [PATH] [--name NAME]` registers one — the project of the current directory by default, named after its directory; `remove NAME|PATH` drops one, its files untouched; `list` says where each one stands — `ok`, `missing`, `no-project` or `unreadable`, with why — with the version it pins and the one this machine reads now, and `--json` says the same as one object, `{"format": 1, "projects": [...]}`, for the cockpit; `check` exits 1 when one is not `ok`, and removes nothing. The file is written whole, one write at a time under a lock beside it, and read through the core's grammar: an unknown key, a name that does not match, two entries naming the same directory, a path with a C0 control character or DEL, and a later format are refused. `cortex init` and `cortex sync` — and the sync of `cortex upgrade` — register the project once they succeed, when it is not listed, and say so once; a project moved on disk is registered again, under a free name. A registry that cannot be read or written is a warning, never a failed sync. `CORTEX_NO_REGISTER`, set, keeps these three commands from it — for a CI runner, where `--locked` registers as any sync does, or a throwaway project; `cortex project` still reads and writes it, and `cortex upgrade --all` reads it.
- **`cortex upgrade --all`** (ADR-011 §3.5) moves every registered project, in name order, to the same version — `VERSION`, or the binary's own; none moves down unless `VERSION` names it. A project that is not `ok` is skipped, and one that refuses — overlays that fail, a sync that refuses, a pin newer than the binary — stops none of the others. The command ends with a line per project — moved, already on, refused, skipped — and the files each must commit, and exits 1 if one refused; it asks once whether a newer release is out. `--dry-run` is each project's dry run. With or without `--all`, `--branch NAME` makes the branch in each git project with something to commit, before anything is written — refused where it exists, unless it is the branch checked out, so that the same command run again refuses none it moved — and `--commit --message MESSAGE` commits there the files the upgrade wrote and nothing else, and nothing at all where `cortex.toml`, or a bootstrap file it wrote, held the developer's own changes — the lock, written whole from the pin, does not count; neither is the default, nothing is stashed and nothing is pushed. Interrupted, `--all` ends with a line per project all the same, and exits 130.

### Changed
- **`cortex prune` with no `DIR` reads the registry** (ADR-011 §3.4). It keeps the versions of the projects registered — each one's pin, the version it reads now — the binary's own and each `--keep`. An entry whose project is gone, `missing` or `no-project` with nothing of Cortex left, is dropped from the registry, and said, before any version is removed: a registry that cannot be written stops the command, nothing removed. A `no-project` entry whose `cortex.local.toml` still reads a version — a branch without Cortex checked out — stays, and keeps it. An `unreadable` entry is named, and kept. With nothing registered it exits 2, as it did with no `DIR`; `cortex prune DIR...` works as in 1.4.0, and neither reads nor writes the registry.
- **The instructions file has a rule for a spec that is gone** (ADR-011 §5). When the directory `spec` names does not exist — `cortex prune` removed the version, or the store was emptied — the assistant runs `cortex sync`, as it does for a project not synced; a `cortex/` that links to a checkout moved or deleted, which the sync refuses (#303), it removes first. `cortex upgrade` to 1.5.0 rewrites a project's file with it, unless someone edited the file.
- **The README, rewritten** ([#296](https://github.com/xod-labs/cortex/issues/296)): what Cortex is today, install and setup, every command with its options, the environment variables, and how-tos. The repository's tree moved to [`docs/repository-layout.md`](docs/repository-layout.md).

## [1.4.0] - 2026-11-04 — You Are Here _(Released)_
[Full notes](changelog/1.4.0.md)

### Added
- **`cortex.lock`** (ADR-010 §3.2). Committed beside `cortex.toml`, it holds the version a project resolved and the digest of its spec, one SHA-256 over the files of its three trees. `cortex sync` and `cortex init` write it, and check the store against it: a spec whose files differ is put in the store again, then refused if it still differs. `cortex validate` warns on a missing lock, a lock of another version and a digest that differs; `--strict` fails on them. In CI, `cortex sync --locked` never writes the lock: one missing, or naming another version, fails the job.
- **`cortex upgrade [VERSION]`** (ADR-010 §3.5) moves a project to another version, the binary's own by default. It first says what changes — the agents added, removed and changed, layer by layer — and checks the project's overlays against the target, stopping if they would fail, unless `--force`. It then writes `version`, syncs, rewrites `cortex.lock`, and rewrites the instructions file when nobody changed it — the digest in its marker says so — and the target's template differs; run on the pinned version, it finishes an upgrade cut short — the instructions file, the lock and the machine. With no version, a project pinned after the binary is refused: it moves down only to a version named. `--dry-run` changes nothing, and exits as the run would.
- **`cortex prune DIR...`** (ADR-010 §3.6) removes from the store the versions no project under the directories given pins or reads, up to six levels down, past `.git`, `node_modules`, `vendor`, `.venv` and a synced `cortex/`. The binary's own version and each `--keep` are kept; `--dry-run` only lists. Nothing lists a machine's projects: a project elsewhere gets its version back at its next `cortex sync`.
- **`cortex self-update [VERSION]`** (ADR-010 §3.7) replaces the binary with a release's, the latest by default: the asset of the binary's own build — the x86_64 one on Windows on Arm — checked against the release's `SHA256SUMS`, its host named when it is not GitHub's, run once, then renamed into the running binary's place — on Windows, the old one renamed aside and removed at the next start. Every refusal of a version newer than the binary names it, and `cortex upgrade` with no version says when a newer release is out, unless `CORTEX_NO_UPDATE_CHECK` is set. `--dry-run` replaces nothing.

### Changed
- **`cortex init` reads a kept bootstrap through its marker** ([#141](https://github.com/xod-labs/cortex/issues/141), ADR-010). Each bootstrap template carries `<!-- cortex:bootstrap layout=… format=1 -->` under its title, with a comment that asks to keep it; the file `cortex init` writes adds the digest of what it wrote. A team may retitle or extend the file: `cortex init` reads its layout and format from that line, and no longer takes a retitled workspace bootstrap for a single project's.
- **The assistant keeps the machine in step with the project** (ADR-010 §3.8). When the spec is missing, or of another version than `cortex.toml`'s, the bootstrap has the assistant run `cortex sync` itself — and `cortex self-update` first when the binary is too old for the pin — saying what it runs, where it used to ask the user to. It runs `cortex upgrade` only when the user asks for it. The command is `cortex-ai` when there is one, and `cortex` once `cortex --version` says it is Cortex — looked up only when a rule needs it, in `~/.cortex/bin` too.

## [1.3.2] - 2026-11-03 — Somebody Else's Problem _(Released)_
[Full notes](changelog/1.3.2.md)

### Fixed
- **A required check passes only when every job behind it passed or was skipped** ([#236](https://github.com/xod-labs/cortex/issues/236)). It passed on a job GitHub abandoned for want of a runner, a result GitHub does not document, and the merge queue merged 1.3.1 without its macOS build. ADR-009 records the change.

### Changed
- **The binary replays the CLI's suite in parallel** ([#229](https://github.com/xod-labs/cortex/issues/229)), twice as many workers as CPUs, every test still through the binary. The Windows job takes 232 to 324 s instead of 664 s.

## [1.3.1] - 2026-11-03 — Wildly Inaccurate _(Released)_
[Full notes](changelog/1.3.1.md)

### Fixed
- **`bin/setup-release.sh` reads the rules GitHub applies to each branch** ([#226](https://github.com/xod-labs/cortex/issues/226)), so it reports an organisation's or an enterprise's ruleset, one whose pattern excludes or nests, or one on a second page. It used to match the patterns itself.
- **`bin/check-english.sh` scans and reports a file whatever its path** ([#227](https://github.com/xod-labs/cortex/issues/227)). It used to skip a path that is not ASCII, and to lose the hits of a path holding a `|`.

### Changed
- **A 404 on creating or updating a release says what GitHub documents** ([#225](https://github.com/xod-labs/cortex/issues/225)): `GITHUB_TOKEN` cannot write a release whose commit changes a workflow. Measured on 2026-11-03, GitHub does not enforce it, and the cycle is unchanged. ADR-009 records the measurement.
- **`#<issue>` goes before the subject of any commit that delivers an issue**, ADR task or not ([#174](https://github.com/xod-labs/cortex/issues/174)).

## [1.3.0] - 2026-10-31 — Lunchtime Doubly So _(Released)_
[Full notes](changelog/1.3.0.md)

### Added
- **The release cycle** ([ADR-009](docs/adr/ADR-009-release-cycle.md), [#187](https://github.com/xod-labs/cortex/issues/187)). Every merge into `main` is a release:
  - the `release gate` checks the pull request before it merges — one new version, its note, its section dated the day it merges in UTC, again on the merge queue and each night;
  - the merge is built and left as a draft with its six assets;
  - the maintainer publishes it from the Releases page, and the `release guard` checks every publication — the provenance of each asset, `SHA256SUMS`, immutability, `latest`.
- **Pre-releases are versions** — `X.Y.Z-alpha.N`, `-beta.N`, `-rc.N` — cut by hand from an integration branch, installed and pinned by their version (`install.sh 1.3.0-rc.1`), and never `latest` ([#203](https://github.com/xod-labs/cortex/issues/203)).
- **An older major version lives on `N.x`** and releases through the same cycle, never as `latest` ([#204](https://github.com/xod-labs/cortex/issues/204)).
- **`bin/setup-release.sh`** applies the settings the cycle relies on, and checks them with `--check`: the ruleset, the merge queue, immutable releases ([#199](https://github.com/xod-labs/cortex/issues/199)).

### Changed
- **A binary knows the spec archives of immutable pre-releases**, and checks a pinned pre-release against them ([#205](https://github.com/xod-labs/cortex/issues/205)).
- **No release is made from a tag**: a tag pushed by hand starts nothing ([#202](https://github.com/xod-labs/cortex/issues/202)).
- **Commit messages are checked** on every pull request, and a subject is written in the present tense, saying what the commit does ([#192](https://github.com/xod-labs/cortex/issues/192)).
- **The history of `main` was rewritten on 2026-10-30.** Eight commit messages written after the commit convention of 2026-06-03 broke it, and now follow it. Every tree is unchanged, and the tags 0.6.0 to 1.2.1 moved to the rewritten commits. The previous history stays on GitHub at `refs/archive/pre-rewrite-2026-10-30`, since the attestations and the spec archives of 1.0.0 to 1.2.1 name its commits. A clone made before then must fetch again: `git fetch --force --tags origin`, then `git reset --hard origin/main` on `main`. The installed binaries and the published assets are unchanged. [ADR-009](docs/adr/ADR-009-release-cycle.md) makes it the last rewrite: once releases are immutable, their tags no longer move.

## [1.2.2] - 2026-10-31 — On Display _(Released)_
[Full notes](changelog/1.2.2.md)

### Changed
- **Cortex moved to [`xod-labs/cortex`](https://github.com/xod-labs/cortex)**, from `adorey/cortex` ([#214](https://github.com/xod-labs/cortex/pull/214)). The install scripts, the CLI and the documentation name the new address. The old one redirects, so the binaries of 1.0.0 to 1.2.1 keep working. [Moving to xod-labs](docs/moving-to-xod-labs.md) says what to update, and how to verify the attestations of the versions released before the move (ADR-008 §9).

## [1.2.1] - 2026-10-30 — Don't Panic _(Released)_
[Full notes](changelog/1.2.1.md)

### Changed
- **`cortex-core` needs Python 3.11 or later**, as the CLI and the runtime already did ([#177](https://github.com/adorey/cortex/issues/177)). The 3.9 floor served the shim that ran the core from source on a host, with its system Python (ADR-007 §3.3); the `cortex` binary replaced it in 1.0.0, and a host needs no Python since. CI runs the core's tests on the Python that `ubuntu-latest` and `macos-latest` ship, and on 3.11: the 3.9 job stopped before any test once `ubuntu-latest` became Ubuntu 26.04, which ships no 3.9.

### Fixed
- **The checks come before every repository Cortex tells you to remove** ([#170](https://github.com/adorey/cortex/issues/170)). The migrator whose submodule keeps its repository under `.git/modules/`, and a plain clone at `cortex/`, now get the three checks of #160 first. Before, the migrator got `deinit -f`, which throws away the submodule's changes, then the removal of its repository; the clone got `rm -rf` and no command to check it with. Commits and stashes only that repository held were lost with it.

## [1.2.0] - 2026-10-29 — Share and Enjoy _(Released)_
[Full notes](changelog/1.2.0.md)

### Added
- **The `breaking-bad` personality theme** ([#138](https://github.com/adorey/cortex/pull/138)) — *Breaking Bad* / *Better Call Saul*: nineteen characters covering every role, from @Saul (prompt manager) to @Gretchen (innovation funding advisor), with the six themed workflows `h2g2` has. Activate it with `cortex init --theme breaking-bad`, or `theme = "breaking-bad"` in `cortex.toml` — in `cortex.local.toml` for one developer.
- **Market intelligence roles and workflows** ([#150](https://github.com/adorey/cortex/pull/150)). Two roles in `agents/roles/product/`:
  - `market-analyst` — the buyer before the market, the trigger rather than the need, four rings of competition, adjacency scored rather than argued, every claim graded, and what we will not do stated;
  - `innovation-funding-advisor` — the eligibility test before any scheme, and a two-column reading of R&D and innovation. The rates and windows of a jurisdiction stay in a capability of the project.

  Two workflows in `agents/workflows/intelligence/` that orchestrate them: `market-study`, from scoping to a decision document, and `innovation-funding`, from eligibility triage to submission. One capability, `practices/competitive-intelligence` — sourcing, evidence grading, bias control. In `h2g2`, Zarniwoop plays the market analyst and Hotblack Desiato the funding advisor; in `breaking-bad`, Elliott and Gretchen Schwartz.
- **The `release/` capabilities** ([#161](https://github.com/adorey/cortex/pull/161), `agents/capabilities/release/`): four cards for shipping a versioned project. `semver.md` covers Semantic Versioning 2.0.0 — what bumps what, breaking changes, pre-releases, deprecation; `changelog.md`, Keep a Changelog — the six sections, the `[Unreleased]` buffer, a changelog against release notes; `conventional-commits.md`, the type that drives the bump and the PR title a squash merge keeps; `release-please.md`, the Release PR pattern — config and manifest, monorepos, pre-releases. `architect`, `lead-backend`, `lead-frontend`, `platform-engineer` and `tech-writer` load them.
- **The `release-automation` workflow** (`agents/workflows/engineering/release-automation.md`) — six steps from the versioning decision to the first automated release: strategy and ADR, CI wiring, runtime version exposure, dry-run, documentation, permissions audit.

## [1.1.2] - 2026-10-29 — Know Where Your Towel Is _(Released)_
[Full notes](changelog/1.1.2.md)

### Fixed
- **A submodule whose repository is its own `.git` directory** ([#157](https://github.com/adorey/cortex/issues/157)) — `git submodule add` leaves one when run on an existing clone. It was handled wrongly in these cases:
  - **The migrator's clone.** `git submodule deinit` refuses that repository before git 2.35, and from then on moves it under `.git/modules/`, where nothing removed it. The commands `cortex init` prints now begin with `git submodule absorbgitdirs`, which moves it there on any git, and end with its removal, under the name git gives the submodule. If a repository is in that place already — an earlier submodule's, which `git rm` keeps, or another one's — git cannot move it there: `cortex init` says so, and prints no command that would fail.
  - **The developer who added the submodule that way, after pulling its removal.** Their `cortex/` was taken for a plain clone: it gets the teammate's checks and the removal of the submodule's settings, as #147 gave every other teammate.
  - **A submodule whose index is not its last commit** — staged and never committed, or with a new commit staged. It gets `git rm -f`, which git requires there.
  - **A project in a subdirectory of its repository.** git names its submodule by its path from the top, `app/cortex`. The removal of the submodule's settings, which a teammate gets after the pull, now names that section, where 1.1.1 named `submodule.cortex` — another submodule's, if the top of the repository has one.
- **A submodule in a linked worktree of the project is no longer taken for a worktree** ([#158](https://github.com/adorey/cortex/issues/158)). Its repository is under `.git/worktrees/<worktree>/modules/`, and the refusal printed a `git worktree remove` that could not work. It now gets the submodule's commands, the migrator's or the teammate's. A worktree is told by the `commondir` file of its administrative directory, which no submodule's repository holds: a project in a directory named `worktrees` is not taken for one either.
- **`cortex init`'s refusals name Cortex, and the command to run again** ([#159](https://github.com/adorey/cortex/issues/159)). A link, an unreadable `.synced` or a copy with files of yours at `cortex/` named *cortex sync*, and said to run it again — and so did a sync that lost a race or waited too long for the lock. Run by `cortex init`, relaunching `cortex sync` alone did not finish the job: they now say *the command*.
- **The checks before removing a teammate's `cortex/` list stashes, and commits only the reflog holds** ([#160](https://github.com/adorey/cortex/issues/160)): `--reflog` joins the second check, and `git stash list` is the third. A commit made on a detached HEAD and left behind by a `git submodule update`, or a stash, would have been deleted with `cortex/` after checks that showed nothing. The second check prints each commit's author: the reflog also keeps commits that no Cortex branch holds any more, rewritten or deleted upstream, which are not yours. The migrator gets the three checks too when the submodule's repository is its own `cortex/.git`, which the commands now remove (#157).

## [1.1.1] - 2026-09-30 — Unpleasantly Like Being Drunk _(Released)_
[Full notes](changelog/1.1.1.md)

### Fixed
- **A teammate who pulled the submodule's removal is no longer sent to commands that cannot run** ([#147](https://github.com/adorey/cortex/issues/147)). After such a pull, git has dropped `cortex` from the index and kept the directory, the submodule's repository and its settings: `git submodule deinit` and `git rm` had nothing left to act on, and `cortex sync` refused again. It now tells the two states apart — asking git whether the index still holds the submodule — and prints, for the teammate, the commands that remove what the pull left. Before them come two checks: what changed in `cortex/`, ignored files included — but the theme marker `setup.sh` wrote — and the commits no remote branch holds. If a command 1.0.0 or 1.1.0 printed already removed the submodule's repository, it says instead that git can no longer show what changed.
- **The last submodule leaves no empty `.gitmodules`:** `git rm cortex` empties the file and keeps it tracked, so the commands `cortex init` prints include `git rm -f .gitmodules` when `cortex` was the only submodule.
- The refusal of a `cortex/` that is a submodule, a clone or another directory Cortex did not write names Cortex, not *cortex sync*: `cortex init` prints it too.
- **A binary no longer carries the checksums of pre-releases** ([#151](https://github.com/adorey/cortex/issues/151)): 1.0.0 and 1.1.0 knew `0.0.0-alpha.1`'s, which no binary serves. A pre-release is thrown away or built again under its tag, and a checksum kept for it would have the binaries built meanwhile refuse the new build. A pre-release is checked against its release's `SHA256SUMS` alone, like a release newer than the binary.
- The CLI suite no longer breaks when a theme is added ([#148](https://github.com/adorey/cortex/issues/148)): a test listed the shipped themes by hand, and reads them from its spec now.

## [1.1.0] - 2026-09-30 — Vogon Poetry _(Released)_
[Full notes](changelog/1.1.0.md)

### Added
- **The `code-review` workflow** (`agents/workflows/engineering/code-review.md`) — reviewing someone's pull request (mode A), and handling the review of ours (mode B), with one grid for both sides: a shared legend (🔴 blocking, 🟠 major, ⚪ minor, ❓ doubt, 🎫 out of scope), rules that make a review converge — a 🔴 or a 🟠 is proven in the code, a defect older than the pull request becomes an issue, no new mechanism in answer to a review, a new point from round 2 only on the previous round's delta — the GitHub event each verdict takes, and the template of the published review. Mode A also checks that the base is current, runs the CI again when it ran older workflows, and replays what the description says was verified.

## [1.0.0] - 2026-09-30 — So Long, and Thanks for All the Fish _(Released)_
[Full notes](changelog/1.0.0.md)

### Added
- **The `cortex` command** ([ADR-008](docs/adr/ADR-008-cortex-binary.md)): a native binary for Linux (`x86_64`, `aarch64`, glibc 2.28 or later), macOS (Apple silicon) and Windows (`x86_64`) that needs no Python. `cortex validate` runs the overlay checks of `bin/validate-overlays.sh` — same options, same report, same exit codes.
- **A one-line install**: `curl -fsSL …/install.sh | sh` on Linux and macOS, `irm …/install.ps1 | iex` on Windows. Both check the download against the release's `SHA256SUMS` and install into `~/.cortex/bin`, as `cortex-ai` when another `cortex` comes first on `PATH`. Each release carries the binaries, their `SHA256SUMS` and a build-provenance attestation for each. They download over https only — every redirect too — and install no binary that does not run here.
- **One spec per machine**: `~/.cortex/versions/X.Y.Z`, read-only, filled on demand — the binary's own version from itself, others downloaded from their release's spec archive and checked against its `SHA256SUMS`. A project pins its version in a committed `cortex.toml`; `cortex sync` writes where the spec is in `cortex.local.toml`, which git ignores. The spec is read in place, or brought into the project as `cortex/` with `--link` or `--copy`; `--from PATH` uses a checkout. `cortex validate` validates against that spec. A binary embeds the checksum of every spec archive released before it, and refuses a download its release no longer matches. One sync of a project runs at a time.
- **`cortex init`** — `setup.sh`, at parity: the same options and the same files, byte for byte, plus `cortex.toml`, `cortex.local.toml` and its `.gitignore` line. Services are named by a repeatable `--service`, so `--workspace` runs unattended; an existing instructions file is kept unless `--force`, which keeps it as `FILE.bak` — `FILE.bak.N` when a backup is there: none is overwritten. In a project that has one, `cortex.toml` keeps its version and changes only for the options given. A project whose `cortex/` is a submodule, a worktree or a clone is refused, with the commands that remove it. Beyond parity, `--from PATH` writes the bootstrap from a checkout's templates and syncs from it — the contributors' test loop — and `--theme` also takes a theme of the project's own, in `agents/personalities/`. A project that has a `cortex.toml` keeps the tool whose instructions file is there; a new one takes Copilot's, as before. Outside a git repository no `.gitignore` is written.
- **`claude_access`** — on request, `cortex sync` lets Claude Code read the spec in the store without asking for permission. It keeps the store's path in `.claude/settings.local.json` — that entry only, recorded as `claude_entry` in `cortex.local.toml`, and never the developer's own; `cortex init --tool claude` offers it, and `cortex sync --claude-access` and `--no-claude-access` set it for one developer.

### Changed
- 💥 **A project uses Cortex through `cortex.toml`, not a submodule.** The bootstrap templates read the spec from the directory `spec` names in `cortex.local.toml`, and the theme from `cortex.local.toml`, then `cortex.toml`: the active-theme marker is gone. Moving a project over is covered by [the migration guide](docs/migrating-to-the-binary.md).
- The validator lists files in name order — not in the file system's — so that its report is the same on every platform.
- 💥 **`cortex validate` needs a `cortex.toml`.** Where `bin/validate-overlays.sh` read the `cortex/` of the current directory, a project without `cortex.toml` exits `2`; one whose `cortex/` is a submodule or a clone is sent to `cortex init`, which says how to leave it; one whose spec was synced for another version than `cortex.toml` pins is sent to `cortex sync`.
- **The runtime takes a project's base from its `cortex.toml`** ([ADR-002 §9](docs/adr/ADR-002-cortex-runtime.md#9-amendments)): the store's copy of the version it pins, read when a run is accepted and named in the answer, `cortex_version`. `deploy/compose.yaml` mounts the store's `versions/`, and nothing else of it, read-only from `CORTEX_STORE_PATH`. A project without `cortex.toml` resolves as before; a version the store lacks is refused, `422`. Without `CORTEX_THEME` — which `deploy/.env.example` no longer sets — the theme is the one `cortex.toml` names.

### Removed
- 💥 `setup.sh` and `bin/validate-overlays.sh` — `cortex init` and `cortex validate` replace them, with the same options and the same output.
- 💥 `python3 -m cortex_core.validate` and `cortex_core.validate.cli()`, the script's way into the core ([ADR-007 §9](docs/adr/ADR-007-cortex-core.md#9-amendments)): `cortex validate` runs the core's validator.

## [0.10.1] - 2026-09-27 — Mostly Harmless _(Released)_
[Full notes](changelog/0.10.1.md)

### Fixed
- The delivery process showed several issues behind one closing keyword (`Closes #42, #43`), which closes the first one only; it now says one `Closes #NN` per issue — in the process, the `adr-implementation` workflow and the pull request template.

## [0.10.0] - 2026-09-27 — Quite Definitely the Answer _(Released)_
[Full notes](changelog/0.10.0.md)

### Added
- `cortex-core` (`core/`) — the one implementation of the cascade in code: resolution, merge semantics, the capability catalog, prompt assembly and overlay validation, parameterised by a `base_root` ([ADR-007](docs/adr/ADR-007-cortex-core.md)). Standard library only, Python 3.9 or later.

### Changed
- `bin/validate-overlays.sh` is a shim over `cortex-core`: it needs **Python 3.9 or later**, runs Python isolated, and is about 77× faster — same options, same exit codes.
- The validator reports a headerless file at the path of a base (`MISSING_HEADER`) and checks the overlays behind a symbolic link; `--strict` may start failing on them. A headerless `characters.md` stays `NON_OVERRIDABLE`; a `README.md` is documentation.
- The runtime reads ADR-006's team tier, `agents/project-context.md`, and selects capabilities from it, for every role alike — prompts grow accordingly.
- **The agent sees the project** ([ADR-002 §9](docs/adr/ADR-002-cortex-runtime.md#9-amendments)): its system prompt closes on `# Project overview`, `# Workspace services` and `# Project context`. That is 44 to 66 KB of prompt on every model call, and the developer's untracked notes sent to the model provider; the API backend marks the system prompt cacheable. A run's `service` must be a folder of the workspace, or it is refused (`422`).
- The runtime depends on `cortex-core`: install both, `pip install -e ../core -e .` from `runtime/`.

### Fixed
- A header missing its `Base:`, `Scope:` or `Semantic:` key stopped the validator at once; it is reported as `MISSING_FIELD`.
- The validator's verdicts no longer depend on where the project sits — under a directory named `agents` or `cortex` — and a service overlay with no category level that declares `Scope: workspace` gets `SCOPE_MISMATCH`.
- Header values and file names are printed as read, control characters escaped: an overlay can no longer rewrite the report on a terminal.
- The `claude-cli` backend could not start past 128 KiB of prompt: the system prompt now goes in a file, the task on stdin.
- `setup.sh` named every scaffolded service `@my-project`, and stopped on a service in a subfolder.

## [0.9.0] - 2026-09-23 — Beware of the Leopard _(Released)_
[Full notes](changelog/0.9.0.md)

### Added
- `adr-implementation` workflow — a generic, stack-agnostic eight-step pipeline for delivering one phase of an accepted multi-phase ADR.
- `docs/process/adr-implementation.md` — one integration branch per ADR, one stacked pull request per phase, the four-level issue model (epic → milestone → phase → task), labels and gates.
- Maintainer tooling: `bin/setup-labels.sh`, ADR epic / phase / task issue templates and a pull request template.

### Changed
- ADRs delivered in several steps end with a numbered **Phases** section; new `Implemented` status.
- A pull request inside a stack is merged with a merge commit — never squashed or rebased; ADR work puts `#<issue>` before the commit subject.
- The release naming convention — a name per release, an epigraph per note — is written into `CONTRIBUTING.md`.

### Fixed
- The workflows index listed two of the four generic workflows; `frontend-testing` and `support-triage` were missing.
- `CONTRIBUTING.md` described a release process nobody followed: its release steps now match practice — an integration branch, artefacts stacked last with their heading already marked `_(Released)_`, a GitHub Release, tags without a `v` prefix — and its changelog instruction points at `## [Unreleased]` instead of a `## Changes` section that does not exist.
- `CONTRIBUTING.md` and `CLAUDE.md` no longer point at a `validate-cortex.sh` planned for 0.3 and never written; both name `bin/check-english.sh`, the gate CI actually runs.
- 0.8.0 was released without its `_(Released)_` marker.

## [0.8.0] - 2026-08-26 — Marginalia _(Released)_
[Full notes](changelog/0.8.0.md)

### Added
- `practices/` capability category, opening with `code-comments` — comment the *why*, descriptive not narrative, density under the project's rule. Declared `always load` by `lead-backend`, `lead-frontend`, `architect` and `qa-automation`.

### Changed
- `languages/php`, `languages/typescript` and `frameworks/vue` gain a doc-block section covering their own mechanics only, pointing at the shared card; the orphan comment bullet in `lead-backend` now references it.
- `templates/project-context.md.template` prompts for the project's own comment conventions (language, reference format).
- The capability tree is refreshed in all three places that carry it — `README.md`, `agents/capabilities/README.md` and `docs/getting-started.md`; `agents/capabilities/README.md` also names the distinction between stack-resolved and unconditional categories.

### Fixed
- `lead-backend` declared no capability category at all, so the role loaded none — restored to `languages/`, `frameworks/`, `databases/`, `security/` and `practices/`.
- `lead-frontend`'s capability list rendered as a single run-on line (lost newline after `**Categories to load:**`).

## [0.7.0] - 2026-08-18 — End to End _(Released)_
[Full notes](changelog/0.7.0.md)

### Added
- `testing/` capabilities: framework-agnostic `e2e-testing` and `component-testing`; a `frameworks/vue` card; a `frontend-testing` workflow.
- `regulatory-compliance-writer` role + **@Pag** character.

### Changed
- Symfony reference gains an "End-to-end & functional testing" section **and three more traps that pass every gate**; `lead-frontend` and `qa-automation` now load the `testing/` category.

## [0.6.0] - 2026-08-13 — Trapdoor
[Full notes](changelog/0.6.0.md)

### Added
- Five Symfony "security traps that pass every gate"; a `qa-automation` role card.

### Changed
- Translated API error responses (translatable HTTP exceptions, never a raw throw) documented in the Symfony card.

## [0.5.0] - 2026-08-10 — Publishable
[Full notes](changelog/0.5.0.md)

### Added
- A licence and community documentation; CI for the framework layer.

### Changed
- English throughout (including the archive template); line endings pinned to LF.

## [0.4.1] - 2026-07-28 — Reuse before create
[Full notes](changelog/0.4.1.md)

### Added
- A dispatch-protocol step that checks whether an existing script/playbook/job already covers the case before writing a new one.

## [0.4.0] - 2026-07-20 — Team & developer context tiers (ADR-006)
[Full notes](changelog/0.4.0.md)

### Added
- Two aggregated context tiers: team context in `agents/`, developer context at the root — no change to existing root files.

## [0.3.2] - 2026-07-20 — Cortex reads its own instructions
[Full notes](changelog/0.3.2.md)

### Changed
- Self-hosted `CLAUDE.md` so Cortex bootstraps itself.

## [0.3.1] - 2026-07-09 — Docs catch up with the engine
[Full notes](changelog/0.3.1.md)

### Changed
- README and docs now cover both halves of Cortex (framework + runtime).

## [0.3.0] - 2026-07-09 — The runtime comes alive
[Full notes](changelog/0.3.0.md)

### Added
- The Cortex Runtime executable engine (ADR-002/003), persistence & state, API security & trust model (ADR-004), async execution & resilience (ADR-005), webhooks, and deploy/DevEx tooling.

## [0.2.2] - 2026-06-02 — Cortex Runtime direction (ADR-002)
[Full notes](changelog/0.2.2.md)

### Added
- ADR-002 (Cortex Runtime) accepted, with supporting documentation.

## [0.2.0] - 2026-04-28 — Layered overrides
[Full notes](changelog/0.2.0.md)

### Added
- The cascade/overlay convention for all agent layers, the overlay file convention, and `validate-overlays.sh`.

## [0.1.2] — Minimal Dependencies Principle
[Full notes](changelog/0.1.2.md)

### Added
- A "Minimal Dependencies" universal principle; role-card updates.

## [0.1.1] — Starlight Capability & Archiving Revamp
[Full notes](changelog/0.1.1.md)

### Added
- A Starlight capability; Archiving Protocol v2.

## [0.1.0] — First Release
[Full notes](changelog/0.1.0.md)

### Added
- The 3-layer architecture, 15 agent roles, the H2G2 personality theme, the Prompt Manager & dispatch protocol, the workflows layer, and setup & tooling.
