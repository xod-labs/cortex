# Open Design — Best Practices

> **Tool:** [Open Design](https://github.com/nexu-io/open-design) — open-source (Apache-2.0), local-first,
> agent-native design tool: prototypes, mockups, decks, rendered from a design system file.
> A young project that moves fast: **pin the version you run**, read the release notes before an update,
> and check a behavior described here against the version you pinned.

Open Design does not ship a model. It **delegates the agent loop to a coding-agent CLI** installed where
its daemon runs (Claude Code, Codex, Gemini CLI, Cursor and others), and it can also be driven from
inside that agent through its MCP server (`od mcp install <agent>`). Every render reads the active
design system, a `DESIGN.md`. The output is an HTML artifact: a **target to build towards**, not code to
merge.

## 🧩 Concepts

| Concept | What it is |
|---|---|
| **Design system** | A `DESIGN.md` with nine numbered sections (below). Bundled ones ship with the tool; a team's own is added at runtime and stored in the tool's data |
| **Project** | A workspace of artifacts. A project can be created from an existing folder, to let the agent read a codebase |
| **Skill** | A `SKILL.md` the agent picks for the job (web prototype, design brief, design review, …) |
| **Artifact** | The rendered result: HTML (single file), and for decks PDF and other exports |

### The `DESIGN.md` schema

Nine headings, numbered, in this order — the parser matches the `## N.` prefix:

```
## 1. Visual Theme & Atmosphere
## 2. Color
## 3. Typography
## 4. Spacing
## 5. Layout & Composition
## 6. Components
## 7. Motion & Interaction
## 8. Voice & Brand
## 9. Anti-patterns
```

Tokens are real values inside a `:root {}` block; dark mode overrides them under `[data-theme="dark"]`;
component CSS references tokens, never hard-coded colors; the typography section carries the
`Display:` / `Body:` / `Mono:` font labels the catalog reads. The tool's own authoring guide also
requires WCAG AA contrast for text pairs and a `:focus-visible` style on every interactive component.

## 📐 Fundamental principles

### 1. `DESIGN.md` is a contract, generated from the code

Derive the tokens from the product's real stylesheet — the palette, the type scale, the spacing scale,
the component states — not from a screenshot or a brand PDF. Version the file in a repository; the copy
in the tool's data volume is a deployment of it, not its source. When the code's tokens change, the file
changes in the same pull request, or the mockups drift from what can be built.

### 2. A mockup is a target, not an implementation

Generated markup is never pasted into the application. The Lead Frontend maps each element of the
mockup to an existing design-system component, or to a new one specified as such. The hand-off is a
component map and a token diff, not a ZIP of HTML.

### 3. Accessibility is an acceptance criterion of the mockup

Before a mockup is approved:

- every text and UI pair passes contrast in every theme (4.5:1 text, 3:1 large text and non-text)
- focus states are drawn, not left to the browser
- targets are at least 24 × 24 CSS px
- errors, empty, loading and disabled states exist, and none relies on color alone
- the layout holds at 320 CSS px wide
- headings, landmarks and focus order are annotated

Because the artifact is HTML, **run the same axe scan on it** that runs on the product — it catches
contrast and structure before a line of product code exists.

### 4. Real content, real states

Use real copy lengths, real names and addresses (anonymised), the longest translation, the error
message the backend actually returns. A mockup that only shows the happy path at desktop width hides
the redesign's real cost.

### 5. Read the codebase read-only

To let the agent reproduce existing screens, import the repository as a project from a **read-only**
mount. Exploration and mockups work; an attempt to write into the repository fails, by design — new
work goes to a project the tool owns.

### 6. Mind what the agent sees

The agent runs on your model provider's account, like any coding agent: no production data, no
personal data, no secrets in prompts or imported folders. Use anonymised fixtures.

## 🚫 Anti-patterns

```
❌ A DESIGN.md written by hand from a brand PDF — the mockups then show colors the product does not have
❌ The design system edited only in the tool's UI, with no versioned source
❌ Pasting generated HTML into the application instead of mapping it to components
❌ Mockups approved on looks alone, with no contrast, focus or state check
❌ Happy path only, desktop only, lorem ipsum
❌ Running the latest version unpinned and discovering a schema change mid-project
❌ Importing a repository with write access, or with production data in fixtures
```

## ✅ Checklist

```
- [ ] Tool version pinned; DESIGN.md versioned, generated from the product's tokens
- [ ] Nine sections present; tokens in :root; dark mode under [data-theme="dark"]; no hard-coded colors
- [ ] Codebase imported read-only; no personal or production data
- [ ] Mockups cover all states, 320 px and desktop, real content
- [ ] Accessibility acceptance criteria met; axe run on the HTML artifact
- [ ] Hand-off = component map + token diff, reviewed by the Lead Frontend
```

## 🔗 Interactions

- **Lead Frontend** → owns the design system and the hand-off from mockup to components
- **Accessibility Specialist** → reviews mockups against the acceptance criteria
- **Product Owner** → approves the target before it is built
- **Architect** → design-system structure when the redesign changes components, not only styles
