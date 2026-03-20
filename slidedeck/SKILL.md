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
   - How many slides (target 12–25)
   - Section groupings with divider slides between major topic shifts
   - Which slide type fits each point (see Slide Types below)
   - One clear takeaway per slide — don't overload

3. **Build the deck.** Write a single `.html` file with everything inline — CSS in a `<style>` block, JS in a `<script>` block, content in the `<body>`. The only external resources are Google Fonts loaded via `@import` in the CSS.

4. **Open it.** Run `open <path>` so the user sees it immediately.

## Output Location

- If the user specifies a path, use it
- Otherwise write to `local/` at the root of where the agent was launched: `local/<short-descriptor>-deck.html`

---

## Full Design System

Everything below defines the visual language. Follow it exactly for consistent output across decks.

### Google Fonts Import

Always include this as the first line inside the `<style>` block:

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
```

- **Inter** — used for all body text, headings, labels, buttons
- **JetBrains Mono** — used for `<code>` elements and `.code-block` regions

### CSS Custom Properties

Define these on `:root`. Every color in the deck references these variables — never use raw hex values in slide content.

```css
:root {
  /* Backgrounds — darkest to lightest */
  --bg-base: #06090f;        /* page background, nav background */
  --bg-primary: #0b1120;     /* code block inner background */
  --bg-surface: #131c31;     /* card backgrounds */
  --bg-surface-raised: #1a2742; /* hover states on nav buttons */
  --border: #1e2d4a;         /* card borders, table borders, nav border */
  --border-bright: #2a3f66;  /* emphasized borders (rarely used) */

  /* Text — brightest to dimmest */
  --text-primary: #e4eaf4;   /* headings, strong, bold content */
  --text-secondary: #8494ad; /* body text, paragraphs, list items */
  --text-dim: #4e5f7a;       /* eyebrow labels, captions, de-emphasized text */

  /* Semantic accent colors — each has a specific meaning */
  --blue: #5b9cf5;           /* existing systems, information, neutral highlights */
  --blue-dim: rgba(91,156,245,.12);
  --cyan: #36d8c0;           /* key concepts, primary accent, "the new thing" */
  --cyan-dim: rgba(54,216,192,.10);
  --green: #3dd68c;          /* solutions, additions, positive outcomes, things to build */
  --green-dim: rgba(61,214,140,.10);
  --red: #f06b6b;            /* problems, deletions, danger, things to kill */
  --red-dim: rgba(240,107,107,.10);
  --orange: #f0a04b;         /* warnings, caution, gradual/careful changes */
  --orange-dim: rgba(240,160,75,.10);
  --purple: #a48df0;         /* secondary elements, categories, supporting info */
  --purple-dim: rgba(164,141,240,.10);
  --yellow: #efc94c;         /* attention, metrics, callouts */
  --yellow-dim: rgba(239,201,76,.10);
}
```

**Color consistency rule:** If a service/concept is colored blue on slide 3, it stays blue on slide 15. Pick a color per entity at the start and stick with it through the whole deck.

### Global Reset & Body

```css
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: 'Inter', system-ui, sans-serif;
  background: var(--bg-base);
  color: var(--text-primary);
  overflow: hidden;          /* one slide visible at a time, no page scroll */
  height: 100vh;
  -webkit-font-smoothing: antialiased;
}
```

### Typography Scale

```css
h1 { font-size: 3rem; font-weight: 800; line-height: 1.08; letter-spacing: -.035em; }
h2 { font-size: 1.9rem; font-weight: 700; line-height: 1.18; letter-spacing: -.025em; margin-bottom: 6px; }
h3 { font-size: 1.1rem; font-weight: 600; margin-bottom: 10px; }
p, li { font-size: 1rem; line-height: 1.6; color: var(--text-secondary); }
strong { color: var(--text-primary); font-weight: 600; }

code {
  font-family: 'JetBrains Mono', monospace;
  font-size: .85em;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  padding: 1px 6px;
  border-radius: 3px;
  color: var(--cyan);        /* inline code is always cyan */
}
```

### List Styling

Unordered lists use a small dot indicator instead of browser default bullets:

```css
ul { list-style: none; padding: 0; }
ul li { margin-bottom: 8px; padding-left: 18px; position: relative; }
ul li::before {
  content: '';
  position: absolute;
  left: 0; top: 9px;
  width: 5px; height: 5px;
  border-radius: 50%;
  background: var(--blue);   /* default dot color; can override per-section */
}
```

### Utility Classes

These are applied directly in HTML to color text semantically:

```css
.accent { color: var(--cyan); }
.blue   { color: var(--blue); }
.green  { color: var(--green); }
.red    { color: var(--red); }
.orange { color: var(--orange); }
.purple { color: var(--purple); }
.yellow { color: var(--yellow); }
.dim    { color: var(--text-dim); }
```

### Recurring Visual Elements

**Eyebrow label** — small uppercase text above the heading, anchors the slide to its section:
```css
.eyebrow {
  font-size: .72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .14em;
  margin-bottom: 6px;
  /* color is set inline to match the section's accent color */
}
```

**Divider** — thin gradient bar between heading and content, provides visual breathing room:
```css
.divider {
  width: 44px; height: 3px;
  border-radius: 2px;
  margin: 12px 0 22px;
  background: linear-gradient(90deg, var(--blue), var(--cyan));
}
.center .divider { margin: 12px auto 22px; } /* centered variant */
```

**Subtitle** — lighter weight text for secondary messaging under headings:
```css
.subtitle { font-size: 1.2rem; color: var(--text-secondary); font-weight: 300; }
```

---

## Slide Engine

### HTML Structure

The deck is a stack of absolutely-positioned `.slide` divs inside a `.deck` container. Only the `.active` slide is visible:

```html
<body>
  <div class="pbar" id="pbar"></div>       <!-- progress bar, fixed top -->
  <div class="deck" id="deck">
    <div class="slide active" data-slide="0">...</div>
    <div class="slide" data-slide="1">...</div>
    <div class="slide" data-slide="2">...</div>
    <!-- ... -->
  </div>
  <nav class="nav">                        <!-- navigation, fixed bottom -->
    <button id="prevBtn" onclick="go(-1)">← Prev</button>
    <span class="ctr" id="ctr">1 / 20</span>
    <button id="nextBtn" onclick="go(1)">Next →</button>
  </nav>
</body>
```

### Slide CSS

```css
.deck { position: relative; width: 100vw; height: 100vh; }

.slide {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  justify-content: center; align-items: center;
  padding: 48px 64px 72px;          /* 72px bottom clears the nav bar */
  opacity: 0; pointer-events: none;
  transition: opacity .35s ease, transform .35s ease;
  transform: translateY(8px);       /* subtle upward entrance */
}
.slide.active {
  opacity: 1; pointer-events: auto;
  transform: translateY(0);
}
```

### Navigation CSS

```css
.nav {
  position: fixed; bottom: 0; left: 0; right: 0;
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 36px;
  background: rgba(6,9,15,.92);
  backdrop-filter: blur(14px);
  border-top: 1px solid var(--border);
  z-index: 100;
}
.nav button {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  color: var(--text-primary);
  padding: 7px 20px;
  border-radius: 6px;
  font-family: 'Inter', sans-serif;
  font-size: .82rem; font-weight: 500;
  cursor: pointer;
  transition: background .2s, border-color .2s;
}
.nav button:hover { background: var(--bg-surface-raised); border-color: var(--blue); }
.nav button:disabled { opacity: .2; cursor: default; }
.nav button:disabled:hover { background: var(--bg-surface); border-color: var(--border); }
.nav .ctr {
  font-size: .78rem; color: var(--text-dim);
  font-weight: 500; font-variant-numeric: tabular-nums;
}

.pbar {
  position: fixed; top: 0; left: 0; height: 2px;
  background: linear-gradient(90deg, var(--blue), var(--cyan));
  transition: width .4s ease;
  z-index: 100;
}
```

### Navigation JavaScript

Include this at the end of `<body>`, after the nav. It handles keyboard, buttons, touch swipe, and progress updates:

```javascript
let cur = 0;
const ss = document.querySelectorAll('.slide');
const tot = ss.length;
const ctr = document.getElementById('ctr');
const bar = document.getElementById('pbar');
const pb = document.getElementById('prevBtn');
const nb = document.getElementById('nextBtn');

function show(i) {
  ss.forEach(s => s.classList.remove('active'));
  ss[i].classList.add('active');
  ctr.textContent = `${i + 1} / ${tot}`;
  bar.style.width = `${((i + 1) / tot) * 100}%`;
  pb.disabled = i === 0;
  nb.disabled = i === tot - 1;
}

function go(d) {
  const n = cur + d;
  if (n >= 0 && n < tot) { cur = n; show(cur); }
}

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); go(1); }
  if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
  if (e.key === 'Home') { cur = 0; show(cur); }
  if (e.key === 'End') { cur = tot - 1; show(cur); }
});

let tx = 0;
document.addEventListener('touchstart', e => { tx = e.touches[0].clientX; });
document.addEventListener('touchend', e => {
  const d = e.changedTouches[0].clientX - tx;
  if (Math.abs(d) > 60) go(d < 0 ? 1 : -1);
});

show(0);
```

---

## Layout Containers

These control content width and column arrangements inside slides:

```css
.content { width: 100%; max-width: 1100px; text-align: left; }
.center  { text-align: center; }

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  width: 100%; max-width: 1100px;
}
.three-col {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 18px;
  width: 100%; max-width: 1100px;
}
```

---

## Component Library

### Cards

The primary content container. Always has a dark surface background and subtle border. Apply a `.glow-*` class to tint the border and background for semantic meaning.

```css
.card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 22px 26px;
}
.card h3 { color: var(--text-primary); }

/* Semantic glow variants — border gets brighter, background gets a faint tint */
.glow-red    { border-color: rgba(240,107,107,.22); background: linear-gradient(135deg, var(--bg-surface), rgba(240,107,107,.03)); }
.glow-green  { border-color: rgba(61,214,140,.22);  background: linear-gradient(135deg, var(--bg-surface), rgba(61,214,140,.03)); }
.glow-blue   { border-color: rgba(91,156,245,.22);  background: linear-gradient(135deg, var(--bg-surface), rgba(91,156,245,.03)); }
.glow-cyan   { border-color: rgba(54,216,192,.22);  background: linear-gradient(135deg, var(--bg-surface), rgba(54,216,192,.03)); }
.glow-orange { border-color: rgba(240,160,75,.22);  background: linear-gradient(135deg, var(--bg-surface), rgba(240,160,75,.03)); }
.glow-purple { border-color: rgba(164,141,240,.22); background: linear-gradient(135deg, var(--bg-surface), rgba(164,141,240,.03)); }
```

**Usage pattern:** A problem card gets `.glow-red`, a solution card gets `.glow-green`. The heading inside uses the matching color class: `<h3 class="red">Problem</h3>`.

### Code Blocks

For multi-line code, config snippets, or SQL. Darker than cards to visually nest inside them:

```css
.code-block {
  font-family: 'JetBrains Mono', monospace;
  font-size: .82rem;
  background: var(--bg-primary);         /* darker than card surface */
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px 22px;
  line-height: 1.7;
  color: var(--text-secondary);
  overflow-x: auto;
}
```

Use `<span>` with color classes for manual syntax highlighting inside code blocks:
```html
<div class="code-block">
<span class="blue">SELECT</span> * <span class="blue">FROM</span> table
<span class="blue">WHERE</span> id = <span class="accent">:value</span>
<span class="dim">-- comment</span>
</div>
```

### Callout Boxes

Highlighted insight boxes with gradient backgrounds. Three semantic variants:

```css
/* Default — blue/cyan gradient, for key insights and takeaways */
.callout {
  background: linear-gradient(135deg, var(--blue-dim), var(--cyan-dim));
  border: 1px solid rgba(91,156,245,.18);
  border-radius: 10px;
  padding: 16px 26px;
}
/* Danger — for warnings, risks, things that are broken */
.callout-red {
  background: linear-gradient(135deg, var(--red-dim), rgba(240,107,107,.05));
  border-color: rgba(240,107,107,.18);
}
/* Positive — for confirmations, solutions, good news */
.callout-green {
  background: linear-gradient(135deg, var(--green-dim), rgba(61,214,140,.05));
  border-color: rgba(61,214,140,.18);
}
```

**Usage:** Apply both `.callout` and the variant: `<div class="callout callout-red">...</div>`

### Flow Diagrams

Horizontal boxes connected by arrows, used for architecture and process flows:

```css
.flow { display: flex; align-items: center; justify-content: center; gap: 5px; flex-wrap: wrap; margin: 14px 0; }

.fb {                               /* flow box */
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 8px 16px;
  text-align: center;
  font-size: .84rem; font-weight: 500;
  white-space: nowrap;
}

.fa { color: var(--text-dim); font-size: 1.1rem; }  /* flow arrow — just a → character */

/* Before/after variants */
.fb.dead {                           /* something being removed */
  border-color: rgba(240,107,107,.28);
  background: rgba(240,107,107,.05);
  text-decoration: line-through;
  text-decoration-color: var(--red);
}
.fb.new {                            /* something being added */
  border-color: rgba(61,214,140,.28);
  background: rgba(61,214,140,.05);
}
```

**Usage:**
```html
<div class="flow">
  <div class="fb" style="border-color:var(--blue)"><span class="blue">Service A</span><br><span class="dim" style="font-size:.7rem">role</span></div>
  <span class="fa">→</span>
  <div class="fb new"><span class="green">New Service</span></div>
  <span class="fa">→</span>
  <div class="fb dead"><span class="red">Dead Service</span></div>
</div>
```

Color-code flow box borders per service using inline `style="border-color:var(--blue)"`. Add a second line inside the box with `<br><span class="dim" style="font-size:.7rem">subtitle</span>` for role/description.

### Inline Tags

Small badges for categorization, used in tables, next to headings, or in phase descriptions:

```css
.tag {
  display: inline-block;
  font-size: .68rem; font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .07em;
  padding: 2px 8px;
  border-radius: 4px;
  margin-left: 6px;
  vertical-align: middle;
}
.tag-red    { background: rgba(240,107,107,.12); color: var(--red); }
.tag-green  { background: rgba(61,214,140,.12);  color: var(--green); }
.tag-blue   { background: rgba(91,156,245,.12);  color: var(--blue); }
.tag-orange { background: rgba(240,160,75,.12);  color: var(--orange); }
.tag-purple { background: rgba(164,141,240,.12); color: var(--purple); }
.tag-cyan   { background: rgba(54,216,192,.12);  color: var(--cyan); }
```

**Usage:** `<span class="tag tag-red">kill</span>` `<span class="tag tag-green">new</span>` `<span class="tag tag-orange">AWS resource</span>`

### Tables

For ownership maps, status mappings, reference data:

```css
.tbl { width: 100%; border-collapse: collapse; font-size: .88rem; }
.tbl th {
  text-align: left; padding: 9px 14px;
  font-weight: 600; color: var(--text-dim);
  font-size: .72rem; text-transform: uppercase; letter-spacing: .08em;
  border-bottom: 1px solid var(--border);
}
.tbl td {
  padding: 9px 14px;
  color: var(--text-secondary);
  border-bottom: 1px solid rgba(30,45,74,.4);
}
.tbl tr:last-child td { border-bottom: none; }
.tbl tr.kill-row { background: rgba(240,107,107,.04); }  /* highlight rows being removed */
```

### Metric Callouts

Large numbers with labels, used in a `.three-col` grid for impact stats:

```css
.metric { text-align: center; padding: 20px 16px; }
.metric .num { font-size: 2.4rem; font-weight: 800; letter-spacing: -.03em; line-height: 1.1; }
.metric .lbl { font-size: .82rem; color: var(--text-dim); margin-top: 4px; }
```

**Usage:**
```html
<div class="three-col">
  <div class="card"><div class="metric"><div class="num red">3</div><div class="lbl">Services eliminated</div></div></div>
  <div class="card"><div class="metric"><div class="num orange">~120s</div><div class="lbl">Latency removed</div></div></div>
  <div class="card"><div class="metric"><div class="num green">40%</div><div class="lbl">Cost reduction</div></div></div>
</div>
```

### Phased Step Rows

For migration plans, implementation phases, numbered sequences:

```css
.pr { display: flex; align-items: flex-start; margin-bottom: 14px; }
.pb {                                /* phase badge — circle with number */
  display: inline-flex;
  align-items: center; justify-content: center;
  width: 34px; height: 34px;
  border-radius: 50%;
  font-weight: 700; font-size: .9rem;
  margin-right: 14px;
  flex-shrink: 0;
}
.pr .pt { flex: 1; }                /* phase text container */
.pr .pt p { margin-top: 3px; font-size: .92rem; }
```

**Usage:**
```html
<div class="pr">
  <span class="pb" style="background:var(--blue-dim);color:var(--blue)">1</span>
  <div class="pt">
    <strong>Phase Name</strong> <span class="tag tag-green">zero risk</span>
    <p>Description of what happens in this phase.</p>
  </div>
</div>
```

---

## Slide Types

Use the right type for the content. Mix types to keep the deck visually varied — never have 3 identical layouts in a row.

### Title Slide
**When:** First slide and last slide (summary). Centered, large `h1`, subtitle, optional banner.

```html
<div class="slide title-slide active" data-slide="0">
  <div class="logo">COMPANY / TEAM NAME</div>
  <h1>Main Title<br><span class="accent">Highlighted Part</span></h1>
  <div class="divider" style="margin:18px auto;"></div>
  <p class="subtitle">One-line description of the deck</p>
  <!-- Optional verdict/tagline banner: -->
  <div class="verdict-banner">Key verdict or thesis statement</div>
</div>
```

Requires:
```css
.title-slide { text-align: center; }
.logo {
  font-size: .76rem; font-weight: 700;
  letter-spacing: .18em; text-transform: uppercase;
  color: var(--blue); margin-bottom: 24px;
}
/* Optional verdict banner — for strong thesis statements */
.verdict-banner {
  display: inline-block;
  background: linear-gradient(135deg, rgba(240,107,107,.07), rgba(240,160,75,.07));
  border: 1px solid rgba(240,107,107,.18);
  border-radius: 8px;
  padding: 10px 28px;
  font-size: 1.05rem; font-weight: 600;
  color: var(--red);
  margin-top: 20px;
}
```

### Section Divider
**When:** Before each major topic shift. Minimal — large colored word, section label, one-line subtitle. These are pacing beats, not content slides.

```html
<div class="slide section-slide" data-slide="N">
  <p class="eyebrow" style="color:var(--green)">Section 3</p>
  <div class="section-num green">Proposed Solution</div>
  <p class="subtitle">S3 pages, RabbitMQ push, clean ownership</p>
</div>
```

Requires:
```css
.section-slide { text-align: center; }
.section-num {
  font-size: 5rem; font-weight: 800;
  letter-spacing: -.06em; line-height: 1;
  margin-bottom: 12px;
}
```

### Content Slide (Standard)
**When:** Most slides. Uses the eyebrow → h2 → divider → body pattern with a `.content` wrapper.

```html
<div class="slide" data-slide="N">
  <div class="content">
    <p class="eyebrow" style="color:var(--blue)">Section 1 — Current State</p>
    <h2>Slide Heading That States the Conclusion</h2>
    <div class="divider"></div>
    <!-- Body content: cards, tables, flows, etc. -->
  </div>
</div>
```

### Two-Column Comparison
**When:** Before/after, problem/solution, option A vs B. Use glow variants to color-code sides.

```html
<div class="two-col">
  <div class="card glow-red">
    <h3 class="red">Before / Problem / Option A</h3>
    <p>Content...</p>
  </div>
  <div class="card glow-green">
    <h3 class="green">After / Solution / Option B</h3>
    <p>Content...</p>
  </div>
</div>
```

### Three-Column Features
**When:** Parallel concepts at equal weight — design decisions, tradeoffs, feature categories.

```html
<div class="three-col">
  <div class="card glow-red"><h3 class="red">Thing 1</h3><p>...</p></div>
  <div class="card glow-orange"><h3 class="orange">Thing 2</h3><p>...</p></div>
  <div class="card glow-purple"><h3 class="purple">Thing 3</h3><p>...</p></div>
</div>
```

Keep content roughly balanced across all three columns — uneven heights look sloppy.

---

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
- Cards in a row should be roughly balanced in content height — visually uneven grids look broken
- Tables should not exceed ~8 rows per slide — if longer, split or summarize
- Code blocks should not exceed ~10 lines per slide — show the key snippet, not the whole file
- After writing the file, open it with `open <path>` so the user can review immediately
