# Open Design — Best Practices

> **Tool:** [Open Design](https://github.com/nexu-io/open-design) — open-source (Apache-2.0), local-first,
> agent-native design tool: prototypes, mockups, decks, rendered from a design system.
> A young project that moves fast — this card was checked against v0.27 (November 2026): **pin the version
> you run**, read the release notes before an update, and check a format described here against the
> tool's own [design system authoring guide](https://github.com/nexu-io/open-design/blob/main/docs/design-systems.md)
> for the version you pinned. That guide is the source of truth; this card is how a team uses the tool.

Open Design does not ship a model. It **delegates the agent loop to a coding-agent CLI** installed where
its daemon runs (Claude Code, Codex, Cursor, GitHub Copilot CLI and others), and it can also be driven
from inside that agent through its MCP server (`od mcp install <agent>`, read-only by default). Every
render reads the active design system. The output is an HTML artifact — PDF and other exports for decks:
a **target to build towards**, not code to merge.

## 🧩 Concepts

| Concept | What it is |
|---|---|
| **Design system** | A package: a folder `design-systems/<slug>/` holding `manifest.json`, `DESIGN.md` and `tokens.css`. Bundled ones ship with the tool; a folder of your own is picked up the same way. A folder with a `DESIGN.md` alone still loads, as a legacy fallback |
| **Project** | A workspace of artifacts. A project can be created from an existing folder, to let the agent read a codebase |
| **Skill** | A `SKILL.md` the agent picks for the job (web prototype, design brief, design review, …) |
| **Artifact** | The rendered result: HTML (single file), and for decks PDF and other exports |

### The design system package

| File | Holds |
|---|---|
| `manifest.json` | Discovery metadata: id (the folder's slug), name, category, description, source, and the files of the package |
| `DESIGN.md` | The design's prose, for the agent: intent, decisions, usage. **No fixed schema**: headings that describe the system — visual theme, color roles and contrast intent, typography, spacing and layout, components and their states, motion and reduced motion, accessibility, anti-patterns |
| `tokens.css` | The compiled values: CSS custom properties in a `:root {}` block, the dark variant overriding them under `[data-theme="dark"]`. Component CSS references tokens, never raw colors |

The prose and the tokens state the same decisions: an accent, a type scale or a spacing rhythm named in
`DESIGN.md` has its value in `tokens.css`. The tool's own guide also asks for 4.5:1 contrast for text and
3:1 for large text against the background actually paired with it, and a visible `:focus-visible` style
on every interactive control.

## 📐 Fundamental principles

### 1. The design system is a contract, generated from the code

Derive the tokens from the product's real stylesheet — the palette, the type scale, the spacing scale,
the component states — not from a screenshot or a brand PDF. Version the package in a repository; the
copy the tool loads is a deployment of it, not its source. When the code's tokens change, `tokens.css`
and `DESIGN.md` change in the same pull request, or the mockups drift from what can be built.

### 2. A mockup is a target, not an implementation

Generated markup is never pasted into the application. The Lead Frontend maps each element of the
mockup to an existing design-system component, or to a new one specified as such. The hand-off is a
component map and a token diff, not a ZIP of HTML. The tool can also refactor a codebase's components
itself, through its code-migration plugin: that is a code change like any other — a pull request,
reviewed and tested — never a by-product of a mockup.

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

To let the agent reproduce existing screens, give it the repository from a **read-only** mount or
checkout. Exploration and mockups work; a write into the repository fails, as it should — new work goes
to a project the tool owns, and a code change goes through principle 2's pull request.

### 6. Mind what the agent sees

The agent runs on your model provider's account, like any coding agent: no production data, no
personal data, no secrets in prompts or imported folders. Use anonymised fixtures.

## 🚫 Anti-patterns

```
❌ A design system written by hand from a brand PDF — the mockups then show colors the product does not have
❌ The design system edited only in the tool's UI, with no versioned source
❌ DESIGN.md and tokens.css that disagree — the agent follows one, the product the other
❌ Pasting generated HTML into the application instead of mapping it to components
❌ Mockups approved on looks alone, with no contrast, focus or state check
❌ Happy path only, desktop only, lorem ipsum
❌ Running the latest version unpinned and discovering a format change mid-project
❌ Importing a repository with write access, or with production data in fixtures
```

## ✅ Checklist

```
- [ ] Tool version pinned; the design system package versioned, generated from the product's tokens
- [ ] manifest.json, DESIGN.md and tokens.css present and in agreement; tokens in :root;
      dark mode under [data-theme="dark"]; no raw colors in component CSS
- [ ] Codebase given read-only; no personal or production data
- [ ] Mockups cover all states, 320 px and desktop, real content
- [ ] Accessibility acceptance criteria met; axe run on the HTML artifact
- [ ] Hand-off = component map + token diff, reviewed by the Lead Frontend
```

## 🔗 Interactions

- **Lead Frontend** → owns the design system and the hand-off from mockup to components
- **Accessibility Specialist** → reviews mockups against the acceptance criteria
- **Product Owner** → approves the target before it is built
- **Architect** → design-system structure when the redesign changes components, not only styles
