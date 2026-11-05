# Web accessibility — Best Practices

> **Reference:** [WCAG 2.2](https://www.w3.org/TR/WCAG22/) (W3C Recommendation, level AA unless the project
> declares otherwise) · [ARIA Authoring Practices Guide](https://www.w3.org/WAI/ARIA/apg/) ·
> [WAI tutorials](https://www.w3.org/WAI/tutorials/). National standards (RGAA, EN 301 549, Section 508)
> build on WCAG; the project's `compliance/` card carries the one that binds it.
>
> Framework-agnostic: the examples are plain HTML. The framework card (`frameworks/`) carries the idiom.

Accessible UI is not a feature added on top: it is HTML used for what it means, a keyboard path through
every task, and visual design that does not hide information from anyone. Four principles — **perceivable,
operable, understandable, robust** — and most of the work is in the first two.

## 📐 Fundamental principles

### 1. Native semantics first, ARIA last

Use the element that already has the role, the keyboard behavior and the accessible name: `<button>`,
`<a href>`, `<label>`, `<fieldset>`/`<legend>`, `<dialog>`, `<table>`, `<nav>`, `<main>`, headings.
ARIA only describes; it adds no behavior. When a custom widget is unavoidable, follow the matching APG
pattern **entirely** — roles, states, and the keyboard model.

```html
<!-- ❌ no role, no focus, no Enter/Space -->
<div class="btn" onclick="save()">Save</div>

<!-- ✅ -->
<button type="button" onclick="save()">Save</button>
```

### 2. Every task can be done with a keyboard

- Tab order follows reading order; no positive `tabindex`
- Focus is **always visible** (2.4.7) and **not hidden** behind sticky headers or banners (2.4.11, new in 2.2)
- No keyboard trap, except a modal dialog that traps on purpose and gives focus back on close
- A dragging gesture (map, slider, reorder) has a single-pointer alternative (2.5.7, new in 2.2)

### 3. Manage focus when the screen changes without a page load

Single-page apps break the browser's own announcement of a new page. On route change, move focus to
the new main heading (or announce the title through a live region) and update `document.title`. Opening
a dialog moves focus into it; closing it returns focus to the element that opened it.

### 4. Forms say what they need, and what went wrong

- A visible `<label for>` on every field; a placeholder is a hint, never a label
- Required fields marked in text, not only with a color or an asterisk without a legend
- `autocomplete` tokens on personal data fields (1.3.5): `name`, `email`, `street-address`, `postal-code`
- Errors identified in text, linked to the field (`aria-describedby`), with a suggestion (3.3.1, 3.3.3);
  on submit, move focus to the first error or to an error summary
- Do not ask twice for what the user already gave in the same process (3.3.7, new in 2.2)
- Authentication without a cognitive function test (3.3.8, new in 2.2): allow paste and password
  managers. A step that asks to remember, transcribe or solve something offers another method, or a
  mechanism that does it for the user; recognizing objects, or content the user provided, is the only
  test allowed as it stands. A CAPTCHA also needs an alternative in another modality (1.1.1)

```html
<label for="postcode">Postcode (required)</label>
<input id="postcode" name="postcode" autocomplete="postal-code" required
       aria-invalid="true" aria-describedby="postcode-error">
<p id="postcode-error">Enter a 5-digit postcode, for example 12345.</p>
```

### 5. Color carries no information on its own

- Text contrast ≥ **4.5:1**, large text (24 px, or 18.66 px bold) ≥ **3:1** (1.4.3)
- Non-text contrast ≥ **3:1** for UI component boundaries, focus indicators and meaningful graphics (1.4.11)
- A status, a chart series, a map marker or a required field is also distinguished by text, shape or icon
  (1.4.1)
- Contrast is a property of a **pair** (foreground on its actual background), so the design system defines
  approved pairs as tokens, and every theme — dark mode, a customer palette — is checked pair by pair

### 6. Targets are big enough, content reflows

- Pointer targets at least **24 × 24 CSS px**, or spaced so that a 24 px circle does not overlap a
  neighbor (2.5.8, new in 2.2)
- Content works at **320 CSS px** wide without horizontal scrolling (1.4.10) and at **200 %** zoom (1.4.4)
- Text spacing can be increased without loss of content (1.4.12): no fixed heights on text containers

### 7. Structure is announced, not only drawn

- One `<h1>`, then headings that form an outline — never chosen for their size
- Landmarks: `header`, `nav`, `main`, `footer`; several `nav` get distinct labels
- A skip link to `<main>` as the first focusable element (2.4.1)
- `lang` on `<html>`, and on any passage in another language (3.1.1, 3.1.2)
- A unique, descriptive `<title>` per page (2.4.2)
- Help and contact mechanisms in the same place on every page (3.2.6, new in 2.2)

### 8. Alternatives for what is not text

- Informative images: `alt` that says what the image is for; decorative images: `alt=""`
- Icon-only buttons: an accessible name (`aria-label` or visually hidden text)
- Charts: a data table or a text summary next to the chart
- Maps: the same information as a list (addresses, opening hours, distance), and a search that works
  without the map
- Video: captions; audio: transcript

### 9. Dynamic content reaches screen readers

Toasts, validation messages, counters and async results go through a live region (`role="status"` for
polite updates, `role="alert"` for errors) that exists in the DOM **before** the message is injected
(4.1.3). Loading states are announced, not only drawn as spinners.

### 10. Motion is optional

Respect `prefers-reduced-motion`; anything that moves, blinks or scrolls automatically for more than
five seconds can be paused (2.2.2). No flashing above three times per second (2.3.1).

## 🧩 Third-party components

A select, a date picker, a rich-text editor, a carousel, a map or a chart library is part of your
interface and of your audit. Before adopting one — and before an audit, for the ones already in place:

| Question | If the answer is no |
|---|---|
| Does it follow the APG pattern, keyboard included? | Wrap it, or replace it |
| Can its accessible name, description and error state be set from outside? | Wrap it |
| Is its contrast themeable with your tokens? | Override, or replace |
| Is there a native element that does the job (`<select>`, `<input type="date">`, `<dialog>`)? | Prefer the native element |

## 🚫 Anti-patterns

```
❌ <div>/<span> with a click handler instead of <button> or <a href>
❌ outline: none (or a focus style below 3:1) with nothing in its place
❌ tabindex="1" and above
❌ aria-label that contradicts the visible text (2.5.3 Label in Name)
❌ role="button" without Enter and Space handling
❌ Placeholder as the only label
❌ Error messages shown in red only, or only on blur, or never announced
❌ "Click here" / "Read more" links with no context
❌ Headings chosen by font size
❌ A modal that does not trap focus, or loses it on close
❌ An accessibility overlay script as the remediation plan
```

## ✅ Checklist

```
- [ ] Native elements used; custom widgets follow their APG pattern, keyboard included
- [ ] Whole task doable with the keyboard; focus visible, in order, never hidden
- [ ] Focus managed on route change and on dialogs (in, trapped, returned)
- [ ] Every field labeled; errors in text, linked, announced, with a suggestion; autocomplete tokens set
- [ ] Contrast pairs ≥ 4.5:1 text, ≥ 3:1 large text and non-text; nothing conveyed by color alone
- [ ] Targets ≥ 24 × 24 CSS px; reflow at 320 px; 200 % zoom; text spacing
- [ ] One h1, a heading outline, landmarks, skip link, lang, unique titles
- [ ] Alternatives for images, icons, charts, maps, media
- [ ] Live regions for dynamic messages; reduced motion respected
- [ ] Third-party components assessed, wrapped or replaced
```

## 🔗 Interactions

- **Accessibility Specialist** → owns conformance; this card is what they audit against
- **Lead Frontend** → applies these rules in components and the design system
- **QA Automation** → the part of these rules a machine can check, see `testing/accessibility-testing.md`
