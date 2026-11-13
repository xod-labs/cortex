# Delivering an ADR — branches, issues and gates

This is how Cortex turns an **accepted ADR that carries numbered phases** into merged work.
It exists because the roadmap ahead is a chain of decision records, several of which are too
large for one pull request: a single feature branch for that much work is unreviewable, and
a pile of unrelated pull requests loses the thread of the decision.

A small ADR, or one that lands in a single change, does not need any of this: use a normal
`feat/*` branch and a single pull request, as [CONTRIBUTING.md](../../CONTRIBUTING.md#-branch--pr-workflow)
describes.
Its size and its review budget ([§10](#10-size-and-review-budget)) apply all the same, as they do
to every release.

> The role-by-role pipeline — who does what, and the checklist each step must satisfy — is
> the generic workflow [agents/workflows/engineering/adr-implementation.md](../../agents/workflows/engineering/adr-implementation.md).
> This document owns the **mechanics**: branches, issues, labels, commits, gates. The two are
> complementary and neither repeats the other.

The model was proven on an earlier project over two multi-phase ADRs and more than a hundred
issues. The traps called out below are the ones that cost real time there.

---

## 1. Branch model

| Branch | Cut from | Lifetime | Pull request |
|---|---|---|---|
| `main` | — | permanent | target of the **final** pull request only |
| `release/adr-NNN-slug` | `main` | one ADR | pushed immediately, **no pull request until every phase has landed** |
| `docs/adr-NNN-slug` | the ADR release branch | one ADR | the ADR text itself — the **first** pull request of the stack |
| `adr-NNN/phase-N-slug` | the previous branch of the stack | one phase | targets its parent branch |
| `release/X.Y.Z` | the branch it ships | one release | changelog and release note — stacked **last** |

Two branch shapes start with `release/` and they are not the same thing:

- `release/adr-NNN-slug` carries **content** — the integration branch of one decision record,
  alive as long as the ADR takes.
- `release/X.Y.Z` carries **no content** — `changelog/X.Y.Z.md` and the `CHANGELOG.md` section.
  It is stacked last so the note describes what is actually there, and because every pull request
  that touches `CHANGELOG.md` edits the same anchor under `## [Unreleased]`: stacked last, it
  merges clean.

The ADR text goes first because the phases quote it. A reviewer reads the decision before the
code that implements it, in the same stack.

## 2. Stacking the phases

Each phase branches off the **tip of the previous branch in the stack**, never off the release
branch once a phase exists — otherwise phase 3 cannot see what phase 1 introduced.

```text
main
 └── release/adr-007-cortex-core                  (pushed, no pull request yet)
      └── docs/adr-007-cortex-core                PR -> release/adr-007-cortex-core
           └── adr-007/phase-1-core-library       PR -> docs/adr-007-cortex-core
                └── adr-007/phase-2-resolver      PR -> adr-007/phase-1-core-library
                     └── ...
```

GitHub retargets a child pull request automatically when its parent merges, so the stack
collapses one level at a time without manual edits.

**Rules that keep a stack survivable:**

- **Merge a pull request inside a stack with a merge commit.** Squashing rewrites the parent's
  commits, so every branch above it loses its base and has to be rebuilt by hand. GitHub's
  *Rebase and merge* does the same — it always creates new commits — so it is only safe if every
  branch above is then restacked by hand. A merge commit keeps the commits the next branch is built
  on. **`main` takes merge commits only**, the last pull request of a stack included: the ruleset
  allows nothing else ([ADR-009 §3.10](../adr/ADR-009-release-cycle.md#310-the-repositorys-settings--binsetup-releasesh)),
  and a squash would drop from `main` the commits a pre-release is tagged on.
- **Delete the branch once its pull request is merged.** That deletion is what makes GitHub
  retarget the child pull request onto the merged one's base; with automatic deletion off, the
  stack does not collapse until someone clicks *Delete branch*.
- **Restack with `git rebase --update-refs`** when a lower branch is amended: run it on the top
  branch and every branch pointer below it moves with it. Then `git push --force-with-lease`,
  never a bare `--force`.
- **Test-merge the whole stack locally before claiming it is conflict-free.** A stack that is
  green branch by branch can still conflict on the way down.
- **One phase in flight at a time.** Two open phases on the same stack means restacking twice
  for every review comment.
- **A phase does not wait for its own review to unblock the next one.** Start phase N+1 on the
  tip as soon as phase N is code-complete; review comments land as new commits and the restack
  carries them up.

## 3. Issue model — four levels

| Level | Object | Carries | Created |
|---|---|---|---|
| Roadmap | **epic issue**, template *ADR epic*, label `adr-epic`, titled `ADR · <title>` until the record is written, then `ADR-NNN · <title>` | the decision, what it excludes, its dependencies — dated in the Project | as soon as the ADR is on the roadmap |
| ADR | **milestone** `ADR-NNN — <title>` | the whole record and its progress bar | when the ADR is drafted |
| Phase | **parent issue**, template *ADR phase*, sub-issue of the epic | the phase scope quoted from the ADR, its acceptance criteria, its branch | when the ADR is accepted |
| Task | **sub-issue**, template *ADR task* | one testable outcome, one criterion that can fail | with its phase |

**Why an epic above the milestone.** A milestone is not an item of a GitHub Project, so it
cannot become a bar on a roadmap. The epic is what puts an ADR on the plan months before it is
written, and it is what the phases hang from once they exist: progress then rolls up task →
phase → epic.

**Decompose late.** Only the epics exist for the whole roadmap. The phases of an ADR do not
exist until the ADR is written, so their issues are created then — creating them earlier
produces issues that rot.

Tasks are **native GitHub sub-issues**, not a Markdown checklist in the phase issue. A checklist
item cannot be assigned, labelled, referenced by a commit or closed by a pull request; a
sub-issue can, which is what makes the board readable as a changelog afterwards.

`gh` has no sub-issue command. Create the issues normally, then attach each child with the
GraphQL mutation — both node ids first, then one call per child:

```bash
nid() { gh api graphql -f query="query{repository(owner:\"xod-labs\",name:\"cortex\"){issue(number:$1){id}}}" \
        --jq '.data.repository.issue.id'; }

gh api graphql -H "GraphQL-Features: sub_issues" \
  -f query='mutation($p:ID!,$c:ID!){addSubIssue(input:{issueId:$p,subIssueId:$c}){subIssue{number}}}' \
  -f p="$(nid 12)" -f c="$(nid 13)"
```

## 4. The roadmap Project

The epics live in the GitHub Project **Cortex roadmap**, linked to this repository. It adds
what issues cannot carry on their own:

| Field | Meaning |
|---|---|
| `Status` | where the item stands — the Project's built-in field |
| `Wave` | the roadmap wave: V0 Foundation → V5 Scale & offer, or Transverse |
| `Start` / `Target` | the dates that draw the bar in the **Roadmap** view |

The wave is a Project field and **not a label** on purpose: it moves every time the roadmap is
re-planned, and a label copy of it would silently drift. Labels carry what does not move.

The Roadmap view is the Gantt chart. The Table view, grouped by `Wave`, is the planning view.

## 5. Labels and the definition of ready

Created and kept idempotent by [bin/setup-labels.sh](../../bin/setup-labels.sh) — run it again
after adding a line, it updates instead of failing.

| Label | Meaning |
|---|---|
| `adr-epic` / `adr-phase` / `adr-task` | the issue's level, see §3 |
| `adr:007` | belongs to that decision record — one label per ADR entering implementation |
| `phase:1` … `phase:8` | phase within the ADR, matching the ADR's own numbering |
| `type:spec` | the Markdown cascade — roles, capabilities, personalities, workflows |
| `type:core` | cortex-core, the CLI, packaging and distribution |
| `type:runtime` | the engine — API, agentic loop, stores, security gate |
| `type:test` | tests, fixtures, validators, test tooling |
| `type:security` | security hardening or a security finding |
| `type:perf` | optimisation or a performance finding |
| `type:docs` | documentation, ADR amendment, release note |
| `type:infra` | Docker, CI, deployment, configuration |
| `gate:blocking` | must land before the ADR's own release gate — never deferred |
| `status:blocked` | waiting on something outside the issue; the comment says what |

`type:spec`, `type:core` and `type:runtime` mirror the ADR-002 firewall — the runtime consumes
the spec, the spec never depends on the runtime. A task that needs two of them is usually two
tasks, and the board makes that visible.

The phase number comes from the **ADR**, not from the execution order. If an ADR runs its
phases 1 → 3 → 2, `phase:3` is worked before `phase:2` and the labels stay aligned with the
record.

**Definition of ready.** An issue is not ready to be picked up until it has: a milestone, an
`adr:` label, a `phase:` label, a `type:` label, and **an acceptance criterion that can fail**.
"Add a resolver" is not a criterion; "`cortex validate --strict` exits 0 on a host project
scaffolded by `cortex init`" is.

**Pull requests carry labels too** — the same ones as the issues they deliver. Every pull request
has at least one `type:*`; ADR work adds its `adr:NNN`, its `phase:N` and, when the phase gates the
release, `gate:blocking`. A release pull request carries `type:docs`. The board then filters pull
requests the way it filters issues — and a pull request whose labels differ from its issues' is
usually delivering something the issues do not describe.

## 6. Commits and closing keywords

Commits follow the [convention in CONTRIBUTING.md](../../CONTRIBUTING.md#commit-message-convention)
— gitmoji, Conventional Commits, one line, no body, no trailer. A commit that delivers an issue
— an ADR task, or any other — adds its number immediately before the subject:

```text
<gitmoji> <type>(<scope>): #<issue> <subject>
```

```text
✨ feat(core): #42 resolve the cascade from a configurable base root
♻️ refactor(runtime): #43 consume cortex-core instead of the local resolver
✅ test(core): #44 cover a base root outside the project tree
📝 docs(adr): #48 amend ADR-007 with the phase order
```

The number goes first because the issue's timeline then reads as a changelog of that task, in
order, without opening a single diff.

Commits carry no body, so **closing keywords live in the pull request description**, never in
a commit message — **one keyword per issue**, each on its own line:

```text
Closes #42
Closes #43
```

A keyword applies to the one reference that follows it: `Closes #42, #43` closes #42 and only
mentions #43. The 0.10.0 release pull request carried twenty-eight issues in that form, and
closed one.

⚠️ **They will not close anything on their own in this model.** GitHub closes a linked issue
only when the pull request merges into the **default branch**. Every pull request of a stack
targets the branch below it, never `main` — so the keywords record the link and produce the
sidebar entry, and close nothing. On the project this process comes from, sixty-four issues
stayed open through a release because the keywords were in the stacked pull requests only.

Two ways out, and one of them is mandatory:

- repeat **every** `Closes #NN` line in the final pull request (`release/adr-NNN-slug` →
  `main`), which is the one merge that lands on the default branch; or
- close the issues by hand at release time, from the lists the phase pull requests declared.

## 7. Pipeline per phase

Every phase goes through the same eight steps. The checklists belong to
[the workflow](../../agents/workflows/engineering/adr-implementation.md); the sequence is:

1. **Dispatch** — the phase issue is broken into sub-issues and handed to the leads.
2. **Technical breakdown** — the open design points are closed before any code.
3. **Implementation** — the leads implement, pulling in other roles as the design requires.
4. **QA** — a test that cannot fail proves nothing.
5. **Performance audit** — measured, not asserted.
6. **Security audit** — review, plus active probing of what the phase exposed.
7. **Documentation** — merged with the code it describes.
8. **Pull request** — opened once steps 4 to 7 have signed off, targeting the parent branch.

Reviews then run within the record's budget ([§10.2](#102-the-review-budget)): passes after the
first read the delta only, and a cap reached stops the stack until its contributor decides.

A step that finds a defect sends the phase back to step 3; it does not open a follow-up issue on
a later phase, because the stack would carry the defect upward.

**Merging is never delegated.** The maintainer reviews and merges every pull request of the
stack, including the last one into `main`.

## 8. Gates

- **Phase gate.** All sub-issues done, CI green on the phase branch, QA + performance + security
  signed off, documentation updated. Only then does the pull request open.
- **ADR gate.** An ADR may declare a subset of its phases as mandatory before a release. Nothing
  that depends on the gate starts until those phases have merged into the release branch. Their
  tasks carry `gate:blocking`.
- **Build gate.** A phase's pull request runs the tests from source. The `binary` workflow —
  the four targets built, the suite replayed through each binary, the glibc floor — runs where
  code integrates: on a push to a `release/**` branch, on every pull request into `main` or a
  line's branch `N.x`, on the merge group, and when the `release` workflow builds a release. So
  the stack merges **into its release branch**, never around it into `main`; the binaries go
  green there, and only then does the final pull request open (§9). It builds them once more, on
  what `main` will be. A phase that needs the build before that runs the workflow by hand on its
  branch (*Run workflow*).
- **Release gate** (ADR-009 §3.2). Every pull request into `main` is a release: the `release
  gate` check passes only when it adds one new version, its note and its section dated the day it
  merges, in UTC. It runs on the pull request, on the merge group, and again each night.

## 9. Closing an ADR

1. `release/X.Y.Z` — `changelog/X.Y.Z.md` and the `CHANGELOG.md` section, dated the day the final
   pull request will merge, in UTC — stacked last, so the note describes only what its branch
   contains. The ADR status becomes `Implemented` there, with an amendment entry recording what
   changed during implementation. An ADR is appended to, never rewritten. If the final pull
   request slips to another day, one commit on the integration branch, which the final pull
   request carries, corrects the three dates: the `CHANGELOG.md` section's — the gate refuses any
   other day — and the ADR's implemented date, in its header and in `docs/adr/README.md`.
2. A release that changes the release pipeline publishes a release candidate from the release
   branch first: *Run workflow* on the `release` workflow, `X.Y.Z-rc.1`, then *Publish release*
   (ADR-009 §3.7).
3. Final pull request: `release/adr-NNN-slug` → `main`, opened once the `binary` workflow is
   green on the release branch (§8), describing the whole decision and carrying **every**
   `Closes #NN` line, one per issue (§6). It records the size, the review budget and what was spent
   ([§10.5](#105-what-was-spent-at-closing)), and any decision taken at a cap. The `release gate` names its version. It merges through
   the merge queue, with a merge commit, when its checks are green.
4. **The merge is the release.** The draft `X.Y.Z` appears on the Releases page about a quarter of
   an hour later, with its assets; the maintainer reads it and presses *Publish release*, and the
   `release guard` checks it. Nobody pushes a tag.
5. Every issue delivered is closed — by the final pull request's keywords, or by hand against
   the lists.
6. Phase branches are deleted; the milestone closes on its own once every issue is closed; the
   epic closes with it.
7. An issue delivered only in part stays **open**, with a comment saying what shipped and which
   criterion remains.

## 10. Size and review budget

1.8.0 took five review passes over four days, each followed by counter-review rounds. After the
second pass, most findings were edge cases that the previous pass's own fixes had created: the
review no longer converged, it moved. Nothing said how large the record was before it was
written, nor when to stop. This section does both. It applies to every ADR from ADR-014 on, and
to every release.

### 10.1 The size

Every ADR opens with a `Size` section, right after its header and before §1 — the
[record's structure](../adr/README.md#required-sections) has its form. Five criteria, each
scored S = 1, M = 2 or L = 3:

| Criterion | S | M | L |
|---|---|---|---|
| **Phases** | 1–2 | 3–4 | 5 or more |
| **Public surface** — commands, options, keys, file formats added or changed | 1–2 | 3–5 | 6 or more |
| **User files Cortex writes or rewrites** — the bootstrap, `cortex.toml`, the team's file… | none | one | several, or a format that changes |
| **Environment matrix** — operating systems, links, with or without git, workspace flavours | one case | a few | the whole matrix |
| **Existing ADRs touched or amended** | 0–1 | 2–3 | 4 or more |

A total of 5 to 7 is **S**, 8 to 11 is **M**, 12 to 15 is **L**. An L record first answers one
question in its `Size` section: can it be split? #98 was, into the configuration cascade and the
IDE modes.

The size is set before the first review. While the text is still being written it follows the
text — a phase added is a size recomputed. Once the first review has started, it does not move.

A release that carries no ADR — fixes, content, documentation — is sized the same way, in the
description of its first pull request; its phases are its pull requests.

### 10.2 The review budget

| Size | Review passes | Counter-review rounds per pass |
|---|---|---|
| S | 2 | 2 |
| M | 3 | 2 |
| L | 4 | 2 |

A **review pass** reads the stack and returns findings. A **counter-review round** is one turn of
the [code-review workflow](../../agents/workflows/engineering/code-review.md)'s step B6 on the
fixes of a pass: a reviewer without the context reads the delta, until a round finds no 🔴 nor 🟠
or the rounds are spent. There is no fixed duration: the caps bound the release.

- **Each pass after the first reads the delta only** — what changed since the head it last
  reviewed. A finding older than the delta becomes an issue, never a new round.
- **An approved phase merges** into the release branch at once. A later pass does not reopen it:
  what it would change goes into a later phase, or an issue.

### 10.3 A cap reached is the contributor's call

When a pass or a round would go past its cap, work on the stack stops, and the contributor who
carries the change decides what follows — a person, never an agent: one more pass or round, the
remaining findings as issues on the next patch, or a split of the record. The same goes for
splitting a record at any point. **Nobody exceeds a cap or splits a record without that
decision**, written in the stack's final pull request.

A 🔴 found at the cap still blocks the merge — it is never deferred to an issue. What the
contributor decides is how it gets fixed and reviewed.

### 10.4 Two lessons of 1.8.0

- **A message that names a command is tested by running that command.** A remedy copied from a
  test's new output into its assertion proves nothing: during 1.8.0's reviews, a merged branch
  changed an assertion to a new remedy that did not work.
- **A test never pins the version of the binary that replays it.** At `1.8.0-rc.1`, moving a pin
  to the binary's version was a move; at `1.8.0` it was none, and the release run failed after
  the merge.

### 10.5 What was spent, at closing

The final pull request records the size, the budget, and what was spent — passes, and rounds per
pass — and the ADR's `Size` section gets the same line when its status becomes `Implemented`.
The accepted margin is **one pass, or one class**. When two records in a row land outside it, the
same way, the thresholds are revisited, in a pull request on this document. The criteria evolve
the same way, as experience shows them wrong.
