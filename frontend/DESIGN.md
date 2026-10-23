# DESIGN.md — Voice-First AI

Recorded from the built product. This is the authoritative visual system; extensions
inherit it, and changes to it are deliberate system decisions.

## World

"Cool instrument panel." A frosted, cool-neutral glass surface (light default,
blue-charcoal dark) whose single job is to make agent activity legible. One indigo
accent carries primary actions, selection, and live state. Nothing decorative moves;
motion exists to confirm state.

## Mode

Operate. Every surface is a task: chat with agents, approve mutations, inspect traces,
manage keys. Density is permitted; familiarity wins; expression lives in small,
precise details (status dots, tabular figures, span bars).

## Color

Restrained strategy — neutrals plus one accent.

| Role | Light | Dark |
|---|---|---|
| Page ground | `#eef0f5` | `#0b0d13` |
| Raised surface | `#ffffff` | `#141826` |
| Glass chrome (sidebar/topbar) | white @ 60% + blur 18px | `#10131e` @ 60% + blur 18px |
| Hairline | ink @ 9% | white @ 9% |
| Text primary / secondary / muted | `#151823` / `#525a6e` / `#878ea1` | `#e9ebf4` / `#9ba4ba` / `#667089` |
| Accent (actions, selection, live) | `#4f46e5` | `#818cf8` |
| OK / Warn / Danger | `#16803c` / `#a16207` / `#d13232` | `#34d399` / `#fbbf24` / `#f87171` |

Warn/danger appear only at chip and dot scale. Accent never decorates inactive states.

## Typography

One family: **Geist** (text, UI, headings) + **Geist Mono** (ids, models, code, metrics).
Fixed rem scale, ~1.2 ratio, weight 600 for headings:

- Page title 24px/600 · Section head 18px/600 · Body 13–13.5px · Metadata 10–11px
  semibold, tracked 0.16em uppercase (labels only, never above headings)
- Figures are tabular; mono for identifiers and code; sentence case everywhere

## Layout

- Left sidebar 264px (68px collapsed), glass chrome; topbar 56px glass, sticky
- Content max-width 5xl–7xl, 24–40px gutters
- Tables: full-bleed rows, hairline dividers, 12–14px cells, sticky-capable
- Stats: inline label+figure readout on a hairline rule (no boxes)
- Drawer: 540–640px right slide-over with scrim

## Components

Every interactive element ships default, hover, active (`scale .977`), focus-ring
(2px accent, offset 2), disabled, and loading states. Buttons: accent fill (primary),
solid ink (emphasis), hairline ghost (secondary), danger ghost (destructive). Chips:
10.5px semibold on dim tone fields; dots convey live state only.

## Motion

150–280ms, `cubic-bezier(0.32, 0.72, 0, 1)`. Transitions confirm state; the only
orchestrated moments are the streaming caret, the live dot pulse, voice equalizer
bars, and waterfall bars growing on first render. No page-load choreography.

## Signature

Span waterfall: per-step horizontal bars (model = indigo, tools = green, tool = amber)
on a shared millisecond axis, growing left-to-right on open — the run's story told as
proportion, the page's most-detailed view.
