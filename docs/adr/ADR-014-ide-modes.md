# ADR-014 — The IDE modes: a bootstrap rendered from the resolution, and access to the store for each tool

- **Status:** Proposed
- **Date:** 2026-11-18 (proposed, with the arbitrations of 2026-11-18, §2)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `templates/bootstrap-instructions*.md` (format 4), `cli/cortex_cli/` (`init`, `sync`, `upgrade`, `validate`, `config`, `settings`, and a new module for VS Code's settings beside `claude.py`), `core/cortex_core/project.py` (the key), `runtime/` (the formats it reads), `README.md`, `docs/getting-started.md`, `docs/migrating-to-the-binary.md`
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) §3.5 (the modes) and §9 (`claude_access`) · [ADR-010](ADR-010-lock-and-upgrade.md) §3.1 (the format) and §3.8 (the bootstrap's rules) · [ADR-012](ADR-012-bootstrap-block.md) (Cortex's block) · [ADR-013](ADR-013-configuration-cascade.md) §3.10 (format 3) and §7.1 (this follow-up) · epic [#98](https://github.com/xod-labs/cortex/issues/98), second of its two records · [#137](https://github.com/xod-labs/cortex/issues/137) (the measures) · [#376](https://github.com/xod-labs/cortex/issues/376), [#378](https://github.com/xod-labs/cortex/issues/378)

## Size

| Criterion | Score | Why |
|---|---|---|
| Phases | M (2) | three phases |
| Public surface | M (2) | format 4, `copilot_access`, `--copilot-access`, `copilot_entry` |
| User files Cortex writes | L (3) | the bootstrap's format changes, and VS Code's settings on the machine |
| Environment matrix | L (3) | four tools, three modes, Windows, both workspace flavours |
| Existing ADRs touched | L (3) | ADR-008, ADR-010, ADR-012, ADR-013 |

**13 — L.** Budget: 4 review passes, 2 counter-review rounds each, 100 commits. Split: considered, and kept whole by the contributor who carries it on 2026-11-18 — the rendered block and the access for each tool answer the same question, how a tool reaches the spec, and #376 and #378 touch the template the rendered block rewrites. It closes wave 0.

---

## 1. Context

ADR-013 left the IDE modes to this record (§7.1): *the resolved values rendered into Cortex's block, which removes §3.10's comparisons*. Three facts since make the case.

- **The block asks the model to resolve what the CLI has resolved.** Format 3 has the assistant read `cortex.local.toml`, compare each file's `version` with `synced`, tell a checkout from a store, read where a link leads, and order versions below 1.8.0. Those are eleven rules, and a model misreads them: in #137's measures, Cursor ran `cortex sync` while every version matched — the rule said to run nothing. #378 is two of those rules contradicting each other, and #376 a third that reads a link's target the way PowerShell does not print it.
- **#137 measured the four tools** (`docs/migrating-to-the-binary.md`). Cursor and Codex read the store with no prompt. GitHub Copilot reads like Claude Code: in `store` and `link`, VS Code asks once per session before it reads outside the workspace. `claude_access` answers that for Claude Code (ADR-008 §9); Copilot has nothing.
- **VS Code has the setting.** Copilot Chat 0.72 reads `github.copilot.chat.additionalReadAccessPaths`, *absolute folder paths outside of the workspace that Copilot Chat is allowed to read from without requiring confirmation*, read only. It compares them as written — no `~`, no variable — so the value is one machine's, and it does not apply to a link inside the workspace that leads outside it (microsoft/vscode#336153). Copilot CLI has no setting: `--add-dir` on each run.

## 2. Decision

**Cortex renders its block from the resolution: the values the team's files set — the version, the theme, the Prompt Manager's character, the team's folder — are written into the bootstrap, and the assistant reads only what is the machine's, the spec's path, from `cortex.local.toml`. One comparison is left to it. `copilot_access` lets Copilot read the store with no prompt, as `claude_access` does for Claude Code.**

Arbitrated by the contributor on 2026-11-18:

- **31** — the block carries the team's values only. What is one machine's — the spec's path, a developer's try, a developer's theme — stays in `cortex.local.toml`, read by one short rule. No second file per tool: it would double the matrix to spare the model one small read;
- **32** — `copilot_access`, built as `claude_access` is: a key in `cortex.toml` for the team and in `cortex.local.toml` for one developer, lockable to `false` only (ADR-013, 25);
- **33** — the *linked* mode #98 named, which would serve resolved prompts to a Cortex extension, is out of scope: there is no extension to serve before the unified client protocol (#45). It is a follow-up (§7);
- **34** — size L, kept whole (the `Size` section).

Out of scope: an extension for any IDE, and the protocol it would speak (#45); a team's repository outside the workspace (#327); a setting for Copilot CLI, which has none; Cursor and Codex, which need none.

## 3. Detailed contract

### 3.1 Format 4: the rendered block

The templates of 1.9.0 write format **4** (ADR-010 §3.1); ADR-012's end marker and two parts carry over. Its marker carries the values it was rendered from:

```html
<!-- cortex:bootstrap layout=single format=4 version=1.9.0 theme=h2g2 -->
```

The block names, as plain text the model reads:

- **the version** the team's files resolve to — `cortex.toml`'s, else the team's file's (ADR-013 §3.8) — never a developer's try;
- **the theme** the team's files resolve to, and, unless it is `none`, **the Prompt Manager's character** and the path of its card, `cortex/agents/personalities/{theme}/{card}`, as `characters.md` maps it — the team's overlay of that card when its folder holds one;
- **the team's folder**, `agents/` or the folder `agents` names (ADR-013 §3.6).

It keeps the paths it cannot know: `cortex/` is still the directory `spec` names in `cortex.local.toml`.

### 3.2 What the assistant still reads

The rules of format 3's *Where the spec is* (ADR-013 §3.10) become four:

1. **No `cortex.local.toml`, no `spec` in it, or no directory where `spec` points** → run `cortex sync` at the root, and go no further until it succeeded.
2. **`synced` records another version than the block's** — its `pinned` is not the block's `version` — and `cortex.local.toml` sets no `version` of its own → a teammate moved the project: run `cortex sync`, once; when they still differ, tell the user, and go no further. A `version` in `cortex.local.toml` is the developer's try: compare nothing. `reads = ""` is a checkout: compare nothing. Compare the text as written; never order versions.
3. **`theme` in `cortex.local.toml`** is the developer's own: it replaces the block's theme — then find the Prompt Manager's character in that theme's `characters.md`. Without it, the block's character is you.
4. **`cortex sync` says this cortex is too old**, names a missing team's folder, or refuses a key → as in format 3: the command it names, once, or tell the user.

*Which command*, and *never move a pin of your own accord*, carry over unchanged. The rules for a version before 1.8.0 go: format 4 is written for a team's version of 1.9.0 or later only (§3.4), whose `cortex.local.toml` always has `synced`.

### 3.3 Who writes it

- **`cortex init`** and **`cortex upgrade`** render the block from the team's resolution, as they write format 3 from it today (ADR-013 §3.10). `cortex upgrade` rewrites an untouched block when the values it renders differ from its marker's, at the same version too — a theme the team changed in its file is one.
- **`cortex sync`** never writes a committed bootstrap. A block whose marker's values are not the resolution's is *behind*: sync says so, naming `cortex upgrade` with the team's version, as ADR-013 §3.10 has it say of a template.
- **`cortex validate`** fails `--strict` on a block that is behind, and warns without `--strict`.
- **A block the team changed** is never rewritten (ADR-012); `cortex sync` and `validate` say it is not rendered from the resolution, once.

### 3.4 Which version writes which format

Format 4's templates ship in the spec of 1.9.0, so a project reads the format of its version: 1.9.0 and later get format 4, and a project kept on 1.8.x keeps format 3 from its own spec. A binary of 1.9.0 reads formats 2 to 4 — `cortex upgrade` to 1.9.0 moves an untouched format 3 block to format 4; nothing else changes it. The runtime reads the marker's format to know a bootstrap, not its values.

### 3.5 `copilot_access`

`copilot_access = true` — in `cortex.toml` for the team, in `cortex.local.toml` for one developer, the most specific winning, lockable to `false` only — has `cortex sync` keep one entry in VS Code's `github.copilot.chat.additionalReadAccessPaths`:

- **the value** is the store's versions folder, `~/.cortex/versions` (`CORTEX_HOME`'s when set) as an absolute path: Copilot compares paths as written, and one entry for every version means no rewrite when the pin moves;
- **the file** is VS Code's settings of this machine, never the project's `.vscode/settings.json`, which a team commits: inside a VS Code remote — WSL, SSH, a container — the server's `data/Machine/settings.json` (`~/.vscode-server`, `~/.vscode-server-insiders`); otherwise the user's `settings.json` of each VS Code installed — `Code` and `Code - Insiders`, under `~/.config/` on Linux, `~/Library/Application Support/` on macOS, `%APPDATA%` on Windows;
- **its ownership**: Cortex owns the entry it wrote, recorded as `copilot_entry` in `cortex.local.toml`, and no other, as `claude_entry` (ADR-008 §9). It removes it when access is turned off in every project of this machine's registry (ADR-011) that set it, and leaves an entry the developer wrote;
- **a file it cannot read** — JSON with comments it cannot place the entry in, an array that is not one, a file not writable — is left as it is: sync warns, naming the file and the line to add. It never rewrites a file to drop comments;
- **`cortex init --tool copilot`** asks for it on a terminal, as `--tool claude` asks for `claude_access`; `--copilot-access` sets it unattended, `--no-copilot-access` leaves it off. `cortex sync --copilot-access` writes it in `cortex.local.toml`.

Where it does not help, the docs say so: a `link` still asks — Copilot checks a link inside the workspace by where it is, not where it leads; Copilot CLI takes `--add-dir ~/.cortex/versions` on each run; `copy` needs nothing.

### 3.6 The workspace template

`bootstrap-instructions-workspace.md` follows §3.1 to §3.3: the same values, the services' table below the end marker, the cascade of a service's overlays unchanged.

### 3.7 The template's tests

The template's rules are pinned by a table — a case, the files it sets, and the command the assistant runs or none — replacing the `assertIn`s (#376). The README's table (#373) is checked against it.

## 4. Phases

Execution order **1 → 2 → 3**, in one release, 1.9.0, stacked on #408 (the measures of #137).

### Phase 1 — Format 4: the rendered block

§3.1 to §3.4, §3.6 and §3.7, with #376 and #378.

- `cortex init` and `cortex upgrade` to 1.9.0 write format 4, its marker and its text naming the version, the theme, the Prompt Manager's character and card, and the team's folder; an untouched format 3 block is moved; a changed one is kept and said once
- a team's theme changed in its file: `sync` says the block is behind, naming `cortex upgrade` with the team's version; `upgrade` rewrites it at the same version; `validate --strict` fails until then
- a project kept on 1.8.x keeps format 3, byte for byte, through a 1.9.0 binary's `sync`, `upgrade --dry-run` and `validate`
- a developer's `theme` and `version` in `cortex.local.toml` never reach the committed block
- the table of §3.7 pins each of §3.2's rules, for both templates; #378's contradiction is gone with the rule it was in, and #376's link rule with the link reading
- a measure per tool (#137's protocol) reaches the Prompt Manager's card with format 4, in `store`

### Phase 2 — `copilot_access`

§3.5.

- `copilot_access = true` writes the store's versions folder into the right file — a VS Code remote's machine settings, else each desktop install's user settings — and records `copilot_entry`; `false`, or the key removed, removes the entry Cortex wrote and no other
- a settings file with comments has the entry placed without losing one; a file it cannot place it in is untouched, and the warning names the file and the line
- `cortex.team.toml` locks it to `false` only; `config` says where its value comes from; `init --tool copilot` asks on a terminal, `--copilot-access` and `--no-copilot-access` do not
- measured: VS Code's Copilot reads the store in `store` with no prompt once the entry is there

### Phase 3 — Windows, and the docs

- the trials on a real Windows machine: `init`, `sync`, `upgrade` to format 4, `copilot_access` against `%APPDATA%\Code\User\settings.json`, a link and a copy, and the team's folder link written by `config.py` and `sync.py` (#376)
- `README.md`, `docs/getting-started.md` and `docs/migrating-to-the-binary.md` say what each tool needs, the table of #137 updated with format 4

## 5. Consequences

### Positive

- The assistant compares one version, where format 3 had it follow eleven rules: the misreadings #137 saw have no rule left to misread.
- A team's values are read as text, not resolved: a model that cannot order versions does not need to.
- Copilot joins Claude Code: one key, and the store is read with no prompt.

### Negative

- **The committed block changes when the team's values change**: a theme moved in the team's file is a bootstrap to commit, through `cortex upgrade`. A hand edit of `cortex.toml` leaves the block behind until then, which `sync` says.
- **Cortex writes a file outside the project**, VS Code's settings, which ADR-008 never did — on request only, one entry it owns, and never a file it cannot parse.
- **`copilot_access` does not reach a link**, by Copilot's own check, nor Copilot CLI.

## 6. Alternatives considered

- **A second, git-ignored file per tool for the machine's values** (option (c) of the debrief) — `CLAUDE.local.md` for Claude, and whatever each other tool reads. Rejected (31): it doubles the matrix for one small read the model does anyway.
- **The machine's values rendered into the committed block.** Rejected: one developer's path committed for everyone.
- **`.vscode/settings.json` for `copilot_access`.** Rejected: a team commits it, and the value is one machine's path — VS Code expands no `~` there.
- **The linked mode now.** Rejected (33): an interface with no consumer, to redo once #45 decides the protocol.

## 7. Follow-ups (out of scope for this record)

1. **The linked mode** — resolved prompts served to a Cortex extension, with the unified client protocol (#45).
2. **Copilot CLI** — a setting, if it gets one.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.5 the modes, §9 `claude_access`
- [ADR-010](ADR-010-lock-and-upgrade.md) — §3.1 the format, §3.5 `upgrade`, §3.8 the bootstrap's rules
- [ADR-012](ADR-012-bootstrap-block.md) — Cortex's block and the team's part
- [ADR-013](ADR-013-configuration-cascade.md) — §3.6 the team's folder, §3.8 which version reads what, §3.10 format 3, §7.1
- [docs/migrating-to-the-binary.md](../migrating-to-the-binary.md) — #137's measures
- [VS Code's AI settings](https://code.visualstudio.com/docs/copilot/reference/copilot-settings) and microsoft/vscode#336153 — the setting, and the link it does not reach

## 9. Amendments

### While the phases were implemented

- **2026-11-18, phase 1** (§3.1 to §3.4, §3.6, §3.7). What the text left to the code, chosen by the contributor who implemented the phase:
  - **The marker** carries the digest after the format, then the values: `<!-- cortex:bootstrap layout=single format=4 digest=… version=1.9.0 theme=h2g2 -->`. The digest covers the values, as it covers the rest of the block: a value changed by hand is a team's change. A marker of format 4 names no folder and no character; a block whose folder or character differs from the resolution is told behind by its text, against the team's version's template.
  - **The template holds every word.** Its values are placeholders — `{{version}}`, `{{theme}}`, `{{folder}}`, `{{character}}`, `{{card}}` — and the lines that name the Prompt Manager sit between `<!-- cortex:character -->` and `<!-- cortex:character end -->`, which the rendering removes. A binary renders a later version's template with no text of its own. A template of format 3 has no placeholder: it is written byte for byte, whatever the binary.
  - **No character is named** where the theme's `characters.md` holds no row for `prompt-manager` with a 📄 link to a card of the theme that is there: those lines are left out, and the step finds the character in `characters.md`, as format 3 had it. A guessed name is worse than none.
  - **The card** is the team's folder's when it holds one — an overlay, or a theme of the team's own — else the spec's: `agents/personalities/h2g2/Oolon-Colluphid.md`, or `cortex/agents/personalities/h2g2/Oolon-Colluphid.md`. Paths are from the project root, the workspace root in a workspace.
  - **The developer's theme is rule 3 of §3.2, in the personality step**, not in *Where the spec is*: it replaces a theme only where the team has one. A team on `none` has no personality step, as in format 3. *Where the spec is* keeps three rules.
  - **A key refused** runs `cortex self-update`, then `cortex sync`, once each, then tells the user: the block orders no version, and a binary that refuses a key of a project of 1.9.0 is older than it.
  - **The developer's own bootstrap** — git ignores it, or the root is in no repository — is rendered from the full resolution, a try's version and the developer's theme, as ADR-013 §3.10 writes it from that version. Decision 31 is of the committed block; this one is not committed.
  - **Behind** is told from the marker first: a block of format 4 whose marker names another version or theme than the team's resolution is behind, with no spec read — `cortex validate` tells it without the store. Otherwise the block is compared with what the team's version's template renders.
  - **Said once** is one note per file and per run of `cortex sync` and `cortex validate`, only where a changed block's marker names other values than the resolution's. It is a note, not a warning: a changed block is never behind (ADR-013 §3.10), and `--strict` does not fail on it. `cortex upgrade` says a changed block kept when its values are not the target's, at the same version too. `cortex init` says it of a kept file whose marker's values are not the resolution's, naming `cortex upgrade` with the team's version.
  - **Which format a version writes** is its spec's templates' own: no binary compares a version with 1.9.0 to choose it. A project kept on 1.8.x reads format 3 from its spec, and a binary of 1.9.0 writes it byte for byte.
  - **The README's table** of what the assistant does is rewritten for format 4 in phase 1, not phase 3: §3.7 checks it against the template's table.
  - **The runtime** reads no bootstrap marker: §3.4's last sentence asks nothing of it.

- **2026-11-18, phase 2** (§3.5). What the text left to the code, decided by the contributor who implemented it:
  - **No version gate.** `copilot_access` is read whatever version the project pins, as `claude_access` is: the binary acts on it, not the spec. A binary before 1.9.0 refuses the key as unknown, and its refusal already leads to `cortex self-update`.
  - **The entry is the machine's, shared by the projects that set the key.** A project that finds the entry another project of the registry recorded as its `copilot_entry` takes it for Cortex's, and records it too: without that, the second project would take it for the developer's, and never remove it. *Every project that set it*, in §3.5, is read as every project that still sets the key **and** records the entry: a project that sets it but never synced since holds nothing of it, and keeps nothing alive. A registry that cannot be read keeps the entry, with a warning, and the next sync tries again.
  - **One entry in every mode.** The entry is the store's, so a project in `copy` keeps it while it sets the key, where `claude_access` drops its entry for a copy. In `link`, sync says the entry does not reach `cortex/`, naming `cortex sync --store`.
  - **The developer's entry** is one that covers the store as Copilot compares it — the versions folder, or a folder that holds it, written as an absolute path: sync then writes nothing, and owns nothing. An entry written as Cortex writes it, in a file where Cortex did not write it, is the developer's — but the ownership is recorded per machine, not per file: with two desktop installs, the developer's identical entry in one and Cortex's in the other are removed together.
  - **The file's form.** The entry is placed as a text edit — the key appended to the settings' object, or the entry to its list — in the file's own indentation, line endings and trailing commas, its byte order mark kept; an empty file, or one of comments only, gets the object. The edit is read back before it is written: every other setting as it was. The list Cortex leaves empty, with no comment in it, goes with its key, as `claude_access` removes its own. A file written read-only is left as it is: replacing it would bypass its mode. A key twice in the file is a file Cortex cannot edit.
  - **Which install.** A VS Code server is there when its folder is, `~/.vscode-server` or `~/.vscode-server-insiders`; a desktop VS Code when its `Code` or `Code - Insiders` folder is — under `XDG_CONFIG_HOME` on Linux when it is set, `APPDATA` on Windows, and `VSCODE_APPDATA` on every platform, as VS Code reads them. No variable of Cortex's moves them. With neither, sync writes nothing, and says so only to the developer who gave `--copilot-access`.
  - **A removal that fails** — a file Cortex cannot read any more — keeps `copilot_entry`, so that the next sync removes the entry once it can.
  - **`cortex init`** asks on a terminal whenever the tool is Copilot, the default tool of a new project included, as `--tool claude` asks; unattended, it gives a tip naming `cortex sync --copilot-access`, as it does for Claude Code. A spec from a checkout, `--from`, keeps the store's entry: the checkout is not covered.
