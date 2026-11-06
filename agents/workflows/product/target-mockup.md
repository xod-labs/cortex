# Workflow: Designing a target mockup before a redesign

<!-- GENERIC WORKFLOW — cortex
     Can be overridden by {project}/agents/workflows/product/target-mockup.md
-->

## 🎯 Triggers

This workflow activates when the prompt contains formulations such as:
- "target mockup", "mockups for the redesign", "clickable prototype of the new …", "wireframe the new …"
- "redesign the site / journey", "visual overhaul", "design system refresh"

> Not this workflow: one new screen or page → `engineering/feature-development.md`.

## 👥 Agents involved

| Step | Role | Responsibility |
|---|---|---|
| 1 | `roles/product/business-analyst.md` + `roles/engineering/lead-frontend.md` | Brief: users, journeys, what motivates the redesign |
| 2 | `roles/engineering/lead-frontend.md` + `roles/engineering/accessibility-specialist.md` | Design-system contract |
| 3 | `roles/engineering/lead-frontend.md` | Mockups of every key screen and state |
| 4 | `roles/engineering/accessibility-specialist.md` | Accessibility review of the mockups |
| 5 | `roles/product/product-owner.md` | Validation, with users |
| 6 | `roles/engineering/lead-frontend.md` + `roles/engineering/architect.md` | Hand-off: component map, token diff, rollout |

**Capabilities:** the design tool the project uses (e.g. `design/open-design.md`),
`practices/web-accessibility.md`, `testing/accessibility-testing.md`, the project's `frameworks/` card.

## 📋 Steps

### Step 1 — Brief
**Agent:** `business-analyst` + `lead-frontend`
**Objective:** A redesign answers named problems, not a taste.

**Checklist:**
- [ ] Users and their tasks, the journeys in scope, the devices they use
- [ ] The evidence that motivates the redesign: audit findings, support tickets, analytics, feedback
- [ ] Constraints: brands or tenants, languages, the standard to meet, what must not change
- [ ] Success criteria that can be checked after release

**Deliverable:** A brief of one page, with the evidence linked.

---

### Step 2 — Design-system contract
**Agent:** `lead-frontend` + `accessibility-specialist`

**Checklist:**
- [ ] The design-system file generated from the product's real tokens and versioned
- [ ] Approved contrast pairs as tokens, a focus-ring token, a minimum target size
- [ ] Component states defined: default, hover, focus, active, disabled, error, loading, empty
- [ ] What the redesign changes in the tokens stated as a diff, not as a new palette from scratch

**Deliverable:** The design-system file the mockups will be rendered from.

---

### Step 3 — Mockups
**Agent:** `lead-frontend` · **Load:** the design tool capability

**Checklist:**
- [ ] Every key screen of the in-scope journeys, desktop and 320 px wide
- [ ] Every state: empty, loading, error, success, long content, the longest translation
- [ ] Real (anonymised) content, no lorem ipsum
- [ ] Annotations: heading outline, landmarks, focus order, accessible names of icon-only controls

**Deliverable:** A set of mockups the team can click through.

---

### Step 4 — Accessibility review
**Agent:** `accessibility-specialist` · **Load:** `practices/web-accessibility.md`

**Checklist:**
- [ ] Contrast pairs in every theme and state; nothing conveyed by color alone
- [ ] Focus visible and drawn; targets ≥ 24 × 24 CSS px, or spaced (2.5.8); reflow at 320 px
- [ ] Error presentation: in text, next to the field, with a suggestion, plus a summary on submit
- [ ] Charts, maps and media have their text alternative in the design, not "to be added later"
- [ ] axe run on the HTML of the mockups when the tool exports HTML
- [ ] Findings fixed in the mockup, not deferred to implementation

**Deliverable:** A review with every blocker closed before validation.

---

### Step 5 — Validation
**Agent:** `product-owner`

**Checklist:**
- [ ] The mockups answer the brief's problems and success criteria
- [ ] A usability check with real users, including people who use assistive technologies when possible
- [ ] Scope and order of delivery decided

**Deliverable:** An approved target, and what is deliberately left out.

---

### Step 6 — Hand-off
**Agent:** `lead-frontend` + `architect`

**Checklist:**
- [ ] Each mockup element mapped to an existing component, a component to change, or a new one
- [ ] The token diff applied in the design system first, then the components, then the pages
- [ ] Tickets per component and per page, with the accessibility acceptance criteria copied in
- [ ] A rollout plan: behind a feature flag, one group of users at a time, when users will notice the change
- [ ] No generated markup pasted into the application

**Deliverable:** A backlog the team can build from, and a rollout the customers can expect.

## ✅ Definition of "done"

- [ ] Brief, design-system contract, mockups, accessibility review and validation exist and are linked
- [ ] Every blocker of the accessibility review is closed in the mockups
- [ ] The hand-off is a component map and a token diff, ticketed

## 🔗 Related workflows

- `engineering/accessibility-audit.md` — the audit whose findings often start a redesign
- `engineering/feature-development.md` — building each ticket of the hand-off
- `engineering/frontend-testing.md` — the tests each rebuilt component gets
