# Workflow: Accessibility audit and its regression harness

<!-- GENERIC WORKFLOW — cortex
     Can be overridden by {project}/agents/workflows/engineering/accessibility-audit.md
-->

## 🎯 Triggers

This workflow activates when the prompt contains formulations such as:
- "accessibility audit", "a11y audit", "WCAG audit", "RGAA audit", "EN 301 549 or Section 508 conformance"
- "conformance rate", "accessibility statement", "are we accessible?"
- "automate accessibility checks", "a11y in CI", "regular accessibility audits"

## 👥 Agents involved

| Step | Role | Responsibility |
|---|---|---|
| 1 | `roles/engineering/accessibility-specialist.md` + `roles/security-compliance/regulatory-compliance-writer.md` | Frame: standard, scope, sample, reference configuration |
| 2 | `roles/engineering/qa-automation.md` + `accessibility-specialist` | Automated baseline |
| 3 | `roles/engineering/accessibility-specialist.md` | Manual audit on the sample |
| 4 | `accessibility-specialist` + `roles/engineering/lead-frontend.md` + `roles/product/product-owner.md` | Classify and prioritize |
| 5 | `roles/engineering/qa-automation.md` + `roles/engineering/platform-engineer.md` | Regression harness within the CI budget |
| 6 | `roles/security-compliance/regulatory-compliance-writer.md` | Statement and improvement plan |
| 7 | `accessibility-specialist` | Recurring cadence |

**Capabilities:** `practices/web-accessibility.md`, `testing/accessibility-testing.md`, the standard a
law imposes on the project (`compliance/accessibility-{jurisdiction}.md`), the project's `frameworks/` card.

## 📋 Steps

### Step 1 — Frame the audit
**Agent:** `accessibility-specialist` + `regulatory-compliance-writer`
**Objective:** Know what is audited, against what, and for whom — before anything is measured.

**Checklist:**
- [ ] Standard and version (WCAG 2.2, or the standard a law imposes and its version); level (AA by default)
- [ ] Who carries the legal obligation (the operator, the vendor, both) and what the contract commits to
- [ ] In-scope surface named explicitly; out-of-scope surface named too
- [ ] Reference configuration: environment, tenant or brand, theme, locale, test accounts
- [ ] Sample built with the standard's method when it has one — mandatory pages + representative pages +
      every step of each complete process, for instance
- [ ] Target level **not** promised before the baseline exists

**Deliverable:** A one-page frame: standard, scope, sample, configuration, what is out of scope.

---

### Step 2 — Automated baseline
**Agent:** `qa-automation` + `accessibility-specialist` · **Load:** `testing/accessibility-testing.md`

**Checklist:**
- [ ] Browser axe scan of every in-scope route, in its key states, at desktop and 320 px
- [ ] Results grouped per rule, per component, per route
- [ ] Run on demand or on a schedule — **not** a required check of pull requests
- [ ] The result committed as the ratchet's baseline file

**Deliverable:** The baseline report and file — the starting point of the burn-down.

---

### Step 3 — Manual audit
**Agent:** `accessibility-specialist`

**Checklist:**
- [ ] Each criterion of the standard, on each page of the sample: conformant / non-conformant /
      not applicable, with the element and the evidence
- [ ] Keyboard-only and screen-reader passes on every complete process
- [ ] Zoom 200 %, reflow at 320 px, contrast in every theme and state
- [ ] The standard's audit tool used when it has one; the rate computed with the standard's formula
- [ ] What was not tested stated explicitly

**Deliverable:** The audit grid and an internal conformance rate, labeled as internal.

---

### Step 4 — Classify and prioritize
**Agent:** `accessibility-specialist` + `lead-frontend` + `product-owner`

**Checklist:**
- [ ] Each finding tagged by root cause: design system, third-party component, page, content, visual design
- [ ] Each finding tagged by severity: blocker, major, minor
- [ ] Design-system and token fixes ranked first — one fix, every page
- [ ] A decision per third-party component: fix, wrap or replace
- [ ] Visual-design findings that need a redesign routed to `product/target-mockup.md`
- [ ] Backlog tickets created, the target level now set from the evidence

**Deliverable:** A remediation backlog, ordered, with the target level and its date.

---

### Step 5 — Regression harness
**Agent:** `qa-automation` + `platform-engineer` · **Load:** `testing/accessibility-testing.md` §CI budget

**Checklist:**
- [ ] Pull request tier: static lint + HTML validation + component axe, no browser, blocking new
      violations only
- [ ] Scheduled tier: nightly browser scan on a deployed environment; alerts or opens an issue on a
      regression; never blocks a pull request
- [ ] The baseline only shrinks; adding to it requires review and a ticket
- [ ] The pull request duration measured before and after — the harness must not slow the pipeline
      noticeably

**Deliverable:** Both tiers green on the baseline, and a trend the team can read.

---

### Step 6 — Statement and improvement plan
**Agent:** `regulatory-compliance-writer` (with `accessibility-specialist` for the evidence)

**Checklist:**
- [ ] The statement in the standard's format, its status derived from the audited rate only
- [ ] Non-conformities, exemptions and the contact mechanism listed
- [ ] The improvement plans — multi-year, yearly — when the standard or the law requires them
- [ ] Who audited (internal or external), when, on which sample — said plainly

**Deliverable:** A statement nobody can contest from the evidence, and a dated plan.

---

### Step 7 — Recurring cadence
**Agent:** `accessibility-specialist`

**Checklist:**
- [ ] Nightly: the scheduled scan and its trend
- [ ] Each quarter: a manual pass on the pages changed since the last one
- [ ] Each major release, each new version of the standard, and at the renewal period the standard
      imposes: a full audit, then the statement updated
- [ ] Each redesign: mockups reviewed before implementation

**Deliverable:** A calendar in the project's tracker, with an owner per entry.

## ✅ Definition of "done"

- [ ] Frame, baseline, manual audit grid and remediation backlog exist and are linked
- [ ] The pull request tier blocks new violations without a browser; the scheduled tier runs nightly
- [ ] The rate and the statement say exactly what was audited, by whom, and what was not
- [ ] The recurring cadence has dates and owners

## 🔗 Related workflows

- `engineering/frontend-testing.md` — the component and E2E rungs the harness plugs into
- `product/target-mockup.md` — when the findings call for a redesign
