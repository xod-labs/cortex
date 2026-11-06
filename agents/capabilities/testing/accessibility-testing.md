# Accessibility testing — Best Practices

> **Engines and tools:** [axe-core](https://github.com/dequelabs/axe-core) (the de facto rule engine) ·
> `eslint-plugin-jsx-a11y` / `eslint-plugin-vuejs-accessibility` / angular-eslint template rules ·
> [html-validate](https://html-validate.org/) · `jest-axe` / `vitest-axe` · Storybook a11y addon ·
> `@axe-core/playwright`, `cypress-axe`, pa11y-ci · Lighthouse CI · NVDA, VoiceOver, TalkBack.
>
> What to build is in `practices/web-accessibility.md`; this card is how to check it, and where.

Accessibility testing has the same pyramid as any other testing, with one difference that shapes
everything: **the machine can only check a minority of the criteria** — about a third of WCAG 2.1 AA's.
By volume it finds more: 57 % of the issues in Deque's 2021 study of its own audits, a vendor's figure.
The rest — is the alt text meaningful, is the focus order logical, is this error message
understandable — needs a person and an assistive technology. So
automation has one job: **stop regressions cheaply**. Conformance is a manual audit's job.

## 🧱 The layers

| Layer | Tool | Catches | Cannot catch | Cost |
|---|---|---|---|---|
| Static lint | `eslint-plugin-*-a11y` | missing `alt`, unlabeled controls, click without keyboard, bad `tabindex`, invalid ARIA in templates | anything decided at runtime | seconds |
| HTML validation | html-validate (a11y rules on) | invalid nesting, missing required attributes and content, duplicate ids | runtime state | seconds |
| Component | axe on a mounted component (`jest-axe`, `vitest-axe`, Storybook test-runner) | names, roles, ARIA states, labels, per state of the component | **contrast, target size, reflow** under jsdom/happy-dom — no layout, no computed colors | seconds (DOM), minutes (Storybook in a browser) |
| Page | axe in a real browser (`@axe-core/playwright`, `cypress-axe`, pa11y-ci) | everything axe knows, contrast included, on real pages and real data | meaning, order, usability | minutes per route |
| Trend | Lighthouse CI | a score over time | conformance — a 100 is not a statement | minutes |
| Manual | keyboard, screen readers, zoom, contrast checker, the standard's audit tool | the criteria no rule covers | — | hours per page |

## ⏱️ CI budget — where each layer runs

A pull request pipeline that gets slow gets bypassed. Browser-based scans are the slow and flaky part:
they **never** sit on the required path of every pull request.

| Tier | Runs | Layers | Budget | Blocking |
|---|---|---|---|---|
| **Pull request** | every push | static lint on changed files, HTML validation, component axe on changed components | the minute, no browser | ✅ yes — on **new** violations only |
| **On demand** | a label or a manual trigger on a UI-heavy pull request | page axe on the routes the change touches | minutes | ❌ advisory |
| **Scheduled** | nightly, against a deployed environment | page axe on every in-scope route, in each key state; Lighthouse CI | as long as it needs | ❌ never blocks a PR — it opens an issue or alerts on a regression |
| **Periodic** | per release, per quarter, on a new version of the standard | manual audit of the sample | days | — |

## 🔒 The ratchet

A product that was never tested will not pass on day one. Do not wait for zero to switch the check on:

1. **Measure** the baseline and commit it as a file: one entry per violation, fingerprinted by
   rule id + route (or component) + a **stable target** (role and accessible name, not an `nth-child`
   CSS path that changes with every layout tweak)
2. **Fail only on violations not in the baseline**
3. The baseline **only shrinks**: a fixed violation is removed from it in the same pull request, and a
   pull request that adds entries needs an explicit reviewer approval and a ticket
4. Track the baseline size as a metric — it is the remediation burn-down

## 🏷️ axe configuration

- Tags for WCAG 2.2 AA: `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`
- `best-practice` rules reported separately, as warnings — they are not WCAG failures
- Disable a rule only with a comment naming the reason and the ticket; never globally to get green
- Scan at least two viewports: desktop, and 320 CSS px wide (reflow)

## 🎭 Scanning dynamic states

A page scanned in its default state hides most of its widgets. For each route, scan after the
interactions that reveal content: open menus and dialogs, an expanded accordion, a form submitted
empty (error state), an async list loaded. In the scheduled journey run, call the scan at those points
(behind a flag the pull-request E2E job leaves off) rather than writing separate scan-only tests: an E2E
job that gates pull requests once stable must not carry a browser scan onto their required path.

**Determinism:** wait for an app-ready signal, not a timeout; disable animations; seed the data;
authenticate once and reuse the storage state; block service workers that intercept navigations.

## 🧪 Manual protocol (per page of the sample)

1. **Keyboard only:** complete the task; check order, visibility, traps, skip link
2. **Screen reader:** NVDA with Firefox or Chrome, VoiceOver with Safari (and TalkBack on mobile):
   headings list, landmarks, form labels, error announcements, live regions
3. **Zoom 200 %** and **320 px** width: nothing lost, no horizontal scroll
4. **Contrast:** every text and UI pair, in every theme, including hover, focus and disabled states
5. **Record** per criterion: conformant, non-conformant, not applicable — with the element and evidence

Use the audit tool of the applicable standard when it has one: it computes the rate the way the
standard defines it and produces the report in the expected format.

## 📊 Reporting

- Per rule, per component and per route — a rule failing on 200 routes is usually one component
- Root cause next to each finding: design system, third-party, page, content
- Trend over time from the scheduled scan, next to the baseline size
- Say what was not tested: browsers, assistive technologies, states, routes

## 🚫 Anti-patterns

```
❌ Browser a11y scans as a required check on every pull request — slow, flaky, soon disabled
❌ Zero violations required on day one, instead of a baseline and a ratchet
❌ Re-baselining to make the build green, without review
❌ Rules disabled globally, or without a reason and a ticket
❌ Scanning only the home page, or only the default state
❌ Trusting a jsdom run for contrast or target size
❌ Treating a Lighthouse score or a tool report as a conformance statement
❌ Fingerprints on generated CSS selectors — every layout change "fixes" and "adds" violations
```

## ✅ Checklist

```
- [ ] Static lint and HTML validation on changed files, blocking new violations
- [ ] Component axe on changed components, in each of their states
- [ ] No browser scan on the required path of pull requests
- [ ] Nightly page scan on a deployed environment, every in-scope route, key states, two viewports
- [ ] A versioned baseline that only shrinks, fingerprinted on stable targets
- [ ] WCAG 2.2 AA tags; best-practice rules as warnings
- [ ] Manual protocol on the sample at the cadence the standard requires
- [ ] Reports per rule / component / route, with what was not tested
```

## 🔗 Interactions

- **Accessibility Specialist** → owns the manual audit and the baseline; this card is the machine part
- **QA Automation** → wires the layers into the pyramid (see `component-testing.md`, `e2e-testing.md`)
- **Platform Engineer** → scheduled jobs, deployed test environment, report storage
- **Lead Frontend** → fixes at the component rung, where the test is cheapest
