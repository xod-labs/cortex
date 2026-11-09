# ADR-012 — The bootstrap file: Cortex's block, then the team's

- **Status:** Implemented
- **Date:** 2026-11-06 (proposed and accepted: the maintainer's arbitrations of 2026-11-06, §2) · 2026-11-06 (implemented, 1.7.0)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `templates/bootstrap-instructions*.md` (format 2, the end marker), `cli/cortex_cli/init.py` (the digest of Cortex's block, `cortex init --force`), `cli/cortex_cli/upgrade.py` (the split and the merge), `cli/tests/fixtures/init/expected.json`, `README.md`, `docs/getting-started.md`, `docs/migrating-to-the-binary.md`
- **Relates to:** [ADR-010](ADR-010-lock-and-upgrade.md) §3.1 (the marker and its digest) and §3.5 step 5 (a bootstrap nobody changed is written again), which this record narrows to Cortex's block · [ADR-006](ADR-006-workspace-shareable-repo.md) (the team's and the developer's context files) · #98 (the IDE modes, which will write resolved prompts into Cortex's block) · #313

---

## 1. Context

ADR-010 gave the bootstrap file — `CLAUDE.md`, `.github/copilot-instructions.md`, `.cursor/rules/cortex.mdc`, `AGENTS.md`, or the file `--tool custom` names — a marker, and the marker a digest of what Cortex wrote. `cortex upgrade` writes the file again when the digest says nobody changed it. Otherwise it keeps the file, with a note, and the target's template never reaches it.

Teams do change it. It is the file their tool reads first, so it is where a rule the tool must always see goes. One rule added, and the file stops following Cortex: every later fix of the template — 1.5.0's rule for a spec that is gone, 1.6.0's link mode and Cursor's frontmatter — has to be carried over by hand, after `cortex init --force` and a look at the `.bak`.

Three kinds of rule end up in that file, and two already have a place the bootstrap reads:

- **A developer's or a machine's own** — a token-saving proxy, a local tool. It belongs in the tool's user-level instructions (`~/.claude/CLAUDE.md` for Claude Code), or in the developer's `project-context.md` at the workspace root (ADR-006).
- **The team's conventions.** They belong in `project-context.md`, or `agents/project-context.md` in a workspace (ADR-006).
- **What the tool must find in its entry file itself**, read before anything else. Today, the only place is the file Cortex owns.

## 2. Decision

**The bootstrap file is in two parts: Cortex's block, from the top of the file to an end marker, then the team's part — everything after it. Cortex writes and rewrites its block. It never touches the team's part.**

- **Format 2** of the templates closes their text with the end marker. The digest in the marker covers Cortex's block only.
- **`cortex upgrade`** writes the target's block again when the block is as Cortex wrote it, whatever the team's part holds, and puts the team's part back after it, byte for byte.
- **`cortex init --force`** writes the block again, and keeps the team's part.
- **The team's part is empty** in a file Cortex writes: no heading, nothing to fill in.

Decided with the maintainer on 2026-11-06:

- **An end marker, and a split and a merge at the upgrade** — not a rule that has the assistant read another file. Such a rule would rest on the model reading one more file at every conversation, which #137 has not measured for Copilot, Cursor and Codex. And the file it would read is the developer's `project-context.md`, which exists.
- **The team's part after the end marker only.**
- **No automatic split of a file written before format 2.** It would serve files of 1.4.0 to 1.6.0, once. Such a file, changed by a team, is kept with a note, and `cortex init --force` writes format 2 once.
- **`cortex init --force` keeps the team's part.**
- **Format 2**, which an older cortex reads as a later format: it loses no team's part but through `--force`, which keeps a `.bak`.
- **A record of its own**, which the IDE modes (#98) will build on.

Out of scope: the configuration cascade and the IDE modes (#98); a rule that reads another file.

## 3. Detailed contract

### 3.1 The two parts

A format 2 bootstrap, as Cortex writes it:

```markdown
# Cortex AI Team

<!-- cortex:bootstrap layout=single format=2 digest=3f2a9c0e71b4d5a8 -->
<!-- Keep the line above as it is: `cortex init` and `cortex upgrade` read it. -->

… Cortex's text …

<!-- Below the next line, this file is yours: `cortex upgrade` and `cortex init --force` keep what you write there. -->
<!-- cortex:bootstrap end -->
```

A Cursor rule opens on its frontmatter, as in 1.6.0; it is part of Cortex's block.

- **The end marker** is the line `<!-- cortex:bootstrap end -->`, whole, after the marker of ADR-010 §3.1. The first one after that marker ends Cortex's block. The line is Cortex's.
- **Cortex's block** runs from the top of the file — a Cursor rule's frontmatter included — to the end of the end marker's line.
- **The team's part** is everything after it: any text, any length, nothing at all. Cortex reads nothing in it. A marker or an end marker written there is the team's text: it neither counts as a second marker nor ends the block.
- **A file with no end marker** after its marker is all Cortex's block, as a format 1 file is.

### 3.2 The digest

The digest of ADR-010 §3.1 is computed as before — the first 16 hexadecimal digits of the SHA-256 of the text, read with LF line endings and no byte order mark, the marker without its digest — over **Cortex's block** instead of the whole file. A line break is any run of carriage returns before a line feed: CR LF, and the CR CR LF a Windows script leaves when it writes text over CR LF. The digest, the markers and the split all read it so, or they would split a file elsewhere than the digest read it. The block ends with its end marker's line break, so the rule that takes the line breaks at the end of the file as one applies only to a file with no end marker.

### 3.3 `cortex init`

- **A new file** is written in format 2, its team's part empty.
- **A file that is there, without `--force`,** is kept, with the note of ADR-010 §3.1, as before. A format 2 file of the right layout is a bootstrap of this version.
- **With `--force`,** the old file is kept as `.bak`, as before. Cortex's block is written again, and **the team's part of the old file follows it**, byte for byte, when the old file has an end marker; the block then takes the old file's line endings and byte order mark, as `cortex upgrade` writes it. A file without an end marker — format 1, or a file a team wrote — has no team's part: it is replaced whole, as before. So is any file when the template has no end marker — a version before format 2, after a move down: the team's part would join Cortex's block, and stays in the `.bak`.

### 3.4 `cortex upgrade`

ADR-010 §3.5, step 5, applies to Cortex's block:

- **The block is as Cortex wrote it** — its digest says so, or, without a digest, it is the pinned version's block. The target's block is written, followed by the team's part as it was. A format 1 file nobody changed becomes format 2, with an empty team's part.
- **The block was changed** — a team wrote above the end marker, or removed it. The file is kept, with the note of ADR-010, which now also says to write one's rules below the end marker of the file `cortex init --force` writes — when it has one — and, of a workspace's file, names `cortex init --force --workspace`: without the option, `cortex init` writes a single project's bootstrap.
- **A target whose template has no end marker** — a move down to a version before 1.7.0 — keeps a file that has a team's part, with a note: written, the team's part would end up inside Cortex's block.
- **A bootstrap with no marker that is neither the pinned version's file nor the target's** is kept with a note, on the pinned version too, instead of being passed over in silence once the pin has moved (#313).
- **Line endings and the byte order mark:** the block is written with the line endings and the mark the file had, as before — a run of carriage returns before a line feed comes back as CR LF. The team's part is put back as it was.

### 3.5 Where a rule goes

The docs say where each kind of rule of §1 goes, and a workspace's services:

| Rule | Where |
|---|---|
| A developer's, or a machine's | The tool's user-level instructions (`~/.claude/CLAUDE.md`, …), or the developer's `project-context.md` at the workspace root |
| The team's conventions | `project-context.md`; in a workspace, `agents/project-context.md` |
| What the tool must find in its entry file | Below the end marker |
| A workspace's services — `@alias`, folder, description | A table below the end marker |

### 3.6 What stays as it is

The marker of ADR-010, its layouts, and where it is read; what `cortex init` and `cortex upgrade` say of a file with no Cortex bootstrap, with several markers, or of the other layout; the personality block; a Cursor rule's frontmatter; `--instructions-file`; the rules of `--commit` (ADR-011 §3.5).

## 4. Phases

### Phase 1 — Format 2, and the two parts kept

> §3.1 to §3.3, and the merge of §3.4: the end marker in both templates and format 2, the digest over Cortex's block, `cortex init` and `cortex init --force`, and `cortex upgrade` putting the team's part back after the block it writes. The digest stops seeing the team's part in this phase, so the merge comes with it: no phase may write a block over a team's part it then loses.

**Acceptance criteria:**
- both templates close Cortex's text with the end marker and its comment, and carry `format=2`; the workspace template's table of services, which a workspace fills, gives way to a sentence that says it goes below the end marker; the init matrix differs by those lines only
- the digest a written file carries covers its block: text added after the end marker leaves the file untouched; a change above it, or the end marker removed, does not
- a marker or an end marker in the team's part is the team's text
- `cortex init --force` on a file with an end marker writes the block again and keeps the team's part byte for byte, CRLF and byte order mark included, the old file kept as `.bak`; on a file without one, it replaces the file whole
- `cortex upgrade` writes the target's block over a block as Cortex wrote it, and puts the team's part back after it, byte for byte; a format 1 file nobody changed becomes format 2, with an empty team's part
- a file with a team's part is kept, with a note, when the target's template has no end marker; a line break of any number of carriage returns is one to the digest and to the split
- a format 1 file kept without `--force` gets the older-format note; a format 2 file, against a pinned version whose templates are format 1, gets the later-format note, with no `--force` offered
- the docs say where each kind of rule goes (§3.5)

### Phase 2 — What `cortex upgrade` says, and #313

> The rest of §3.4: the notes, and #313.

**Acceptance criteria:**
- a format 2 file whose block a team changed is kept, with a note that names the end marker
- `--dry-run` says a block it would write again keeps the team's part, and writes nothing
- on the pinned version, a bootstrap with no marker that is not the pinned version's file gets a note instead of nothing (#313)
- `--commit`'s ending follows what it committed (#313)
- `--all` and `--commit` keep their rules: a bootstrap written again over uncommitted changes of the developer is not committed

All phases ship in one release, 1.7.0, with #310, #311, #312 and #314.

## 5. Consequences

### Positive
- A team writes the rules its tool must read first in the entry file, below the end marker, and keeps every later fix of the template.
- The IDE modes (#98) get the frontier they need: a block Cortex may render whole, beside the team's text.
- Nothing more for the model to read at each conversation, and nothing that depends on the tool.

### Negative
- **A file written before 1.7.0 that a team changed does not migrate on its own.** One `cortex init --force` writes format 2; the team copies its rules from the `.bak` below the end marker.
- A rule written above the end marker still keeps the block from following Cortex. The note says where to write it.
- A cortex older than 1.7.0 reads a format 2 file as a later format. Its `cortex init` keeps it, with a note, and offers no `--force`; its `cortex upgrade` keeps one that has a team's part, and writes one that has none again in its own format. It loses no team's part but through `--force`, which keeps a `.bak`.

### Neutral
- The digest of a file with no end marker is computed as in 1.4.0, so a format 1 file nobody changed is recognised as before.
- The marker keeps its form; only its `format` changes.

## 6. Alternatives considered

- **A rule in the bootstrap that reads another file** (a `cortex.local.md`, say). It rests on the model following one more instruction at every conversation, which #137 has not measured outside Claude Code, and the developer's `project-context.md` already plays that part. Rejected by the maintainer.
- **A three-way merge** of the old template, the new one and the file. It would take edits anywhere, but its conflicts would land in a file teams do not treat as code, and its result is hard to predict. Rejected.
- **Splitting a file written before format 2 by its digest** — finding the line where Cortex's text ends. It serves files of 1.4.0 to 1.6.0 once, then never. Rejected by the maintainer.
- **A team's part above Cortex's block as well.** Two zones to explain and to merge, and a Cursor rule's frontmatter must stay first. Rejected by the maintainer.

## 7. Follow-ups (out of scope for this ADR)

- **The IDE modes** (#98, its second record): Cortex renders the resolved prompts into its block.
- **The directory of the team's overlays, `agents/`, set per project** — the configuration cascade (#98, its first record).

## 8. References

- [ADR-010](ADR-010-lock-and-upgrade.md) §3.1 and §3.5 — the marker, its digest, and what `cortex upgrade` writes again
- [ADR-006](ADR-006-workspace-shareable-repo.md) — the team's and the developer's context files
- #313 — a bootstrap with no marker passed over in silence once the pin moved
- #306 — Cursor's frontmatter (1.6.0)

## 9. Amendments

The record was *Accepted*, not yet implemented, when these were made: the body above already says each. They are listed against the text Bluspark's counter-review read on 2026-11-06.

### The counter-review of 2026-11-06

- **A workspace's services table goes below the end marker** (§3.5, phase 1). The workspace template held a table to fill inside Cortex's block: filled, it failed the digest, and `cortex upgrade` kept the file, which stopped following Cortex. A sentence now says where the table goes, and which folders are the services when there is none. ADR-001 §3.5, where the Prompt Manager tells workspace mode by the workspace template and a services table, finds the table there, or none: the services are then the folders that hold a `project-overview.md`.
- **The note says where a team's rules go only when the file `cortex init --force` writes has an end marker** (§3.4) — the target's template, which before format 2 has none — and names `--workspace` for a workspace's file, told by its marker or, without one, by its title.
- **`cortex init --force` writes a template without an end marker whole** (§3.3): after a move down to a version before format 2, the team's part would have joined Cortex's block, as `cortex upgrade` refuses to make it.

### 2026-11-07 — `cortex init --force` keeps a workspace's layout

- **§3.4 and §3.6** ([#329](https://github.com/xod-labs/cortex/issues/329)). *Without the option, `cortex init` writes a single project's bootstrap* no longer holds: without `--workspace`, it takes the layout the file it replaces was written for, by its markers or else its title, and says so. The note of `cortex upgrade` still names `--workspace` for a workspace's file. What `cortex init` says of the other layout changes with it: nothing of the instructions file without `--workspace`; of another tool's file, `--tool X --force`, which keeps it as .bak and keeps its team's part.
