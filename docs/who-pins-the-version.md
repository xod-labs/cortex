# Who pins the version?

Since 1.8.1 ([ADR-013](adr/ADR-013-configuration-cascade.md)), the Cortex version a project reads can
come from three files: the project's `cortex.toml`, the team's `agents/cortex.team.toml`, and a
developer's `cortex.local.toml`. This guide walks through the cases a team meets, one at a time:
which file wins, the command that moves it, and what teammates see.

Whatever the case, one command answers *why am I on this version?*:

```bash
cortex config
```

It writes nothing. Each output below was printed by `cortex` 1.8.1. Long store paths are left out
of the sync messages, and `…` marks lines left out of a table.

## The short answer

| Your situation | Who pins the version | The command that moves it |
|---|---|---|
| A single project, or a workspace whose root is a git repository | the committed `cortex.toml` | `cortex upgrade VERSION` |
| A workspace whose root is no repository, with a team's file | `agents/cortex.team.toml`, in the team's repository | `cortex upgrade --team VERSION` |
| A committed `cortex.toml` beside a team's file | `cortex.toml`, when it sets `version`; the team's file otherwise | `cortex upgrade VERSION`, or remove `version` to follow the team |
| You, trying a later version on your machine | `cortex.local.toml`, when it is higher than the team's | `cortex upgrade --local VERSION` |

A try is never lower than the team's version, and `version` is never locked.

## Reading `cortex config`

```text
$ cortex config
version        1.8.1                                          cortex.toml
theme          h2g2                                           cortex.toml
sync           store                                          default
claude_access  unset                                          default
agents         agents                                         default
releases_url   https://github.com/xod-labs/cortex/releases    default
update_check   true                                           default
register       true                                           default
```

Three columns: the setting, its value, and where the value comes from — a file, an environment
variable, or `default`. `— locked` after the origin says the team's file imposes it.

- `cortex config version` prints the value alone: `1.8.1`.
- `cortex config --locked` leaves your `cortex.local.toml` out: it gives the version CI checks and
  the runtime reads.
- `cortex config --json` gives the same as one object, with what each value overrode, whether
  `cortex.toml` is committed, and the warnings.

## Case 1 — A single project

`cortex init` writes `cortex.toml`, which you commit:

```toml
# Written by `cortex init`. Committed: the Cortex version this project uses, and the team's theme.
version = "1.8.1"
theme = "h2g2"
```

That file pins the version for everyone who clones the repository, and `cortex config` says so: `version  1.8.1  cortex.toml`.
A workspace whose root is a git repository — flavour 2.A of
[ADR-006](adr/ADR-006-workspace-shareable-repo.md) — works the same way.

**To move it:** `cortex upgrade VERSION` — `--dry-run` first. It rewrites `cortex.toml`,
`cortex.lock` and an untouched bootstrap, then tells you what to commit:

```text
$ cortex upgrade 1.7.0
…
cortex.lock: Cortex 1.7.0, where it named 1.8.1 — commit it
CLAUDE.md: written again from Cortex 1.7.0's template — no one changed it

Cortex 1.7.0 is ready. Commit cortex.toml, cortex.lock, CLAUDE.md; teammates run `cortex sync` after they pull.
```

## Case 2 — A workspace whose root is no repository

In flavour 2.B, each service is its own repository and the workspace root is in none: its
`cortex.toml` is committed nowhere, so it pins the version for your machine only. The team pins it
in its own repository, `agents/`, with a **team's file**. Nothing creates it for you:

```text
$ cortex upgrade --team 1.8.1
cortex upgrade: no agents/cortex.team.toml — the team creates it, by a commit in its repository. Nothing was changed
```

The team writes and commits `agents/cortex.team.toml`:

```toml
version = "1.8.1"
theme = "h2g2"
locked = ["theme"]
```

From then on, its version is everyone's. A `version` left in the root's `cortex.toml` changes
nothing, and `cortex sync` says so:

```text
$ cortex sync
note: cortex.toml's version, 1.8.1, is at or below the team's 1.8.1 (agents/cortex.team.toml) and changes nothing — remove `version` from cortex.toml
…
agents/cortex.team.lock: written — commit it in agents/
```

Remove `version` and `theme` from the root's `cortex.toml`, and commit the lock in `agents/`. Then:

```text
$ cortex config
version        1.8.1                                          agents/cortex.team.toml
theme          h2g2                                           agents/cortex.team.toml — locked
…
```

**To move it:** `cortex upgrade --team VERSION`, in a workspace that reads the team's file. It
writes the team's file and its lock; `--branch` and `--commit` act in the team's repository.

The team's file is read once the team's version is 1.8.0 or later. Each teammate on a cortex older
than 1.8.0 runs `cortex self-update` once, then `cortex sync`.

## Case 3 — A committed `cortex.toml` beside a team's file

A project that commits its own `cortex.toml` keeps its own version: the committed file wins over
the team's. Here `agents/cortex.team.toml` pins 1.8.1 and theme `breaking-bad`, and the project's
committed `cortex.toml` pins 1.8.1 and theme `h2g2`:

```text
$ cortex config
version        1.8.1                                          cortex.toml
theme          h2g2                                           cortex.toml
…
```

When the committed `cortex.toml` pins a version before 1.8.0, the team's file is not read at all:

```text
$ cortex config
note: agents/cortex.team.toml is not read: the team's version is 1.7.0, before 1.8.0
version        1.7.0                                          cortex.toml
…
```

Remove `version` from the committed `cortex.toml`, and the project follows the team's file. A key
`cortex.toml` still sets, here `theme`, stays its own:

```text
$ cortex config
version        1.8.1                                          agents/cortex.team.toml
theme          h2g2                                           cortex.toml
…
```

## Case 4 — A teammate who has not synced yet

A teammate moved the version and you pulled the commit. Your machine still has the spec of the
version before, and Cortex refuses to read it:

```text
$ cortex validate
cortex validate: cortex.toml pins Cortex 1.8.1, and the spec synced is "1.7.0" — run `cortex sync`
```

`cortex sync` puts the pinned version in the store. Your assistant runs it itself, saying so, when
the bootstrap finds the spec of another version — and, in a workspace with a team's file,
when the versions `cortex sync` last recorded in `cortex.local.toml` no longer match:

```toml
synced = { team = "1.8.1", pinned = "1.8.1", reads = "1.8.1" }    # written by `cortex sync` — the versions it read
```

The assistant never moves the version itself: `cortex upgrade` runs only when someone asks for it.

## Case 5 — Trying a later version on your machine

`cortex upgrade --local VERSION` writes the version in your `cortex.local.toml`, which git
ignores: you read it, nobody else does, and no committed file follows. It is read only when it is
higher than the team's version, and only once the team's version is 1.8.0 or later:

```text
$ cortex upgrade --local 1.8.1
cortex upgrade: a try in cortex.local.toml is read once the team's version is 1.8.0 or later — the team's is 1.7.0. Nothing was changed
```

While you try, every `cortex sync`, `cortex upgrade` and `cortex validate` says you are ahead of
the team. `cortex upgrade --local` at or below the team's version removes the try. When the version
works for you, move the team: Case 1 or Case 2.

## Case 6 — CI

CI reads the team's resolution, without any `cortex.local.toml`, and installs that version:

```text
$ cortex config version --locked
1.8.1
$ cortex sync --locked
…
$ cortex validate --strict
…
Errors:   0
Warnings: 0
```

`cortex sync --locked` never writes a lock: a lock that is missing, or that names another version
than the one pinned, fails the job. [The README](../README.md#run-cortex-in-ci) has the CI lines.

## Locks

The team's file may impose keys by listing them in `locked`. A locked key takes the team's value,
and a developer's own is said and left out:

```text
$ cortex config
note: theme is locked by agents/cortex.team.toml to "h2g2": cortex.local.toml's "breaking-bad" is not read
…
theme          h2g2                                           agents/cortex.team.toml — locked
```

Two keys do not lock like the others:

- **`version` never locks**, so that a developer can try a later version before the team moves:

  ```text
  cortex config: agents/cortex.team.toml: "version" is never locked — moving it is the team's matter, and a developer may try a later one (ADR-013, decision 19)
  ```

- **`claude_access` locks to `false` only**: a team may forbid a permission, never grant one on a
  developer's machine.

  ```text
  cortex config: agents/cortex.team.toml: claude_access is locked to false only — a team may forbid a permission, never grant one on a developer's machine (ADR-013, decision 25)
  ```

## When the answer surprises you

1. Run `cortex config`, and read the third column of `version`.
2. If it names a file you did not expect, find that file's case above.
3. If it says what you expect but your assistant reads another spec, run `cortex sync`.
4. If none of this explains it, open an issue with the output of `cortex config --json`.
