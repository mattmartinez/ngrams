---
name: slidedeck
description: Build polished, self-contained HTML slide deck presentations from source material. Use when the user asks to create a presentation, slide deck, pitch deck, or wants to turn findings, audits, proposals, or technical content into slides. Generates a single .html file with no build tools or external dependencies beyond Google Fonts.
argument-hint: <topic and optional output path, e.g. "migration proposal" or "cost analysis slides to ./slides/cost.html">
---

# slidedeck — HTML Presentation Generator

Build a self-contained HTML slide deck from the current conversation context and/or source material the user provides. The output is a single `.html` file — no build tools, no frameworks, no npm. Open it in a browser and present.

## Directive

$ARGUMENTS

## Process

1. **Gather source material.** Pull from:
   - The current conversation (findings, audits, code traces, decisions, data)
   - Any files or documents the user references — read them with the read tool
   - The user's stated emphasis points, narrative order, or audience

2. **Plan the narrative structure.** Before writing any HTML, decide:
   - How many slides — let the material decide; 12–25 for substantial material, fewer for a thin source. Never pad to hit a count.
   - Section groupings with divider slides between major topic shifts
   - Which slide type fits each point (see Slide Types below)
   - One clear takeaway per slide — don't overload

3. **Build the deck from the template.** Start from `assets/template.html` (see The Template below). Copy it to the output path, then replace the example slides inside `<div class="deck">` with your own. Keep the chrome and `<script>` untouched. Do not hand-write the CSS or JS.

4. **Open it.** Run `open <path>` so the user sees it immediately.

## Output Location

- If the user specifies a path, use it
- Otherwise write to `local/` at the root of where the agent was launched: `local/<short-descriptor>-deck.html`

## The Template

`assets/template.html` (in this skill directory) is the engine and the source of truth. It is a single self-contained file that already wires up:

- the full design system (CSS custom properties, typography, dark + light palettes)
- the slide engine — absolutely-positioned `.slide` stack, prev/next nav, progress bar, one combined `keydown` listener, touch swipe
- the `?` keyboard help overlay
- `N` speaker-notes toggle (per-slide `.speaker-notes` blocks, hidden in print)
- the light/dark theme toggle (persisted in `localStorage` under `slidedeck.theme`)
- the `@media print` block so the deck exports one slide per page

The only external resource is Google Fonts (with a `system-ui` fallback). The template includes **one commented example of every slide type and component** inside the deck — edit those examples in place. Keep the class names, keyboard shortcuts, and chrome exactly as shipped so the runtime behavior stays intact.

## Design System (principles)

- **Fonts:** Inter for all text; JetBrains Mono for `<code>` and `.code-block`.
- **Semantic colors** — each color carries one meaning; use them for that meaning only:
  - `blue` — existing systems, neutral information
  - `cyan` — key concepts, primary accent, "the new thing" (also inline code)
  - `green` — solutions, additions, positive outcomes, things to build
  - `red` — problems, deletions, danger, things to kill
  - `orange` — warnings, caution, gradual/careful change
  - `purple` — secondary elements, categories, supporting info
  - `yellow` — attention, metrics, callouts
- **Color consistency rule:** pick a color per entity at the start and keep it. If a service is blue on slide 3, it stays blue on slide 15.
- **Semantic pairing:** problem cards get `.glow-red`, solution cards get `.glow-green` — consistently, throughout the whole deck.

## Components

All of these live in the template with a worked example — reuse them rather than inventing markup:

- **Cards** (`.card` + `.glow-*`) — the primary content container, tinted for semantic meaning.
- **Callout boxes** (`.callout`, `.callout-red`, `.callout-green`) — highlighted insights and takeaways.
- **Code blocks** (`.code-block`) — multi-line code/config/SQL, with color-class `<span>`s for manual highlighting.
- **Flow diagrams** (`.flow` / `.fb`) — boxes-and-arrows for architecture and process; `.fb.new` / `.fb.dead` for added/removed.
- **Inline tags** (`.tag-*`) — small categorization badges.
- **Tables** (`.tbl`) — ownership maps, status mappings, reference data.
- **Metric callouts** (`.metric`) — large-type numbers in a `.three-col` grid.
- **Phased step rows** (`.pr` / `.pb`) — numbered migration/implementation sequences.
- **Speaker notes** (`.speaker-notes`) — hidden presenter notes toggled with `N`.

## Slide Types

Use the right type for the content. Mix types to keep the deck visually varied — never have 3 identical layouts in a row. Each type has a commented example in the template.

- **Title Slide** — first slide and last slide (summary). Centered, large `h1`, subtitle, optional verdict banner.
- **Section Divider** — before each major topic shift. Minimal: large colored word, section label, one-line subtitle. These are pacing beats, not content slides.
- **Content Slide (Standard)** — most slides. The eyebrow → `h2` → divider → body pattern inside a `.content` wrapper.
- **Two-Column Comparison** — before/after, problem/solution, option A vs B. Use `.glow-*` variants to color-code the sides.
- **Three-Column Features** — parallel concepts at equal weight (design decisions, tradeoffs, feature categories). Keep the columns balanced in height.

## Print / PDF Export

The template's `@media print` block already reveals every slide, one per page. To export: open the deck, **Print → Save as PDF**, choose **Landscape**, and enable **Background graphics** (Chrome) / **Print backgrounds** (Safari) so the dark theme survives — without that toggle the browser drops slide backgrounds and the deck prints on white.

## Narrative Rules

These govern content decisions, not visual styling.

1. **One point per slide.** If you're cramming, split it into two slides.
2. **Section dividers are pacing.** Use them before each major topic shift. They give the audience a mental reset.
3. **Start with a title, end with a summary.** The summary slide recaps key takeaways as a short bulleted list — no new information.
4. **Headlines state conclusions, not topics.** "TAP Has Zero Business Logic" beats "TAP Overview". The body provides evidence.
5. **Use flow diagrams for any process or architecture.** Don't describe data flow in bullets when boxes-and-arrows is clearer.
6. **Use two-column for any comparison.** Side-by-side is always more persuasive than sequential description.
7. **Metric callouts for concrete numbers.** Large-type numbers land harder than numbers buried in paragraphs.
8. **Don't repeat the eyebrow as the heading.** Eyebrow says "Section 2 — Problems", heading says "Bugs Found During Audit" — not "Section 2 Problems".
9. **Match glow colors to meaning consistently.** Red-glowing cards = problems. Green-glowing cards = solutions. Throughout the whole deck.
10. **Vary slide layouts.** Never use three identical layouts in a row. Alternate between two-col, three-col, flow, table, and full-width content.

## Quality Rules

- Every slide must render correctly at 1440×900 viewport without scrolling — if content overflows, split into two slides
- Use real names from the source material (class names, method names, service names, config values) — never generic placeholders
- Every number in a metric callout must appear in the source material — if there is no real figure, use a text card instead
- Cards in a row should be roughly balanced in content height — visually uneven grids look broken
- Tables should not exceed ~8 rows per slide — if longer, split or summarize
- Code blocks should not exceed ~10 lines per slide — show the key snippet, not the whole file
- After writing the file, open it with `open <path>` so the user can review immediately
