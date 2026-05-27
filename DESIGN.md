---
name: Talky
description: Voice-first AI assistant client — quiet by default, alive at the moment.
colors:
  bg-light: "#f6f5f1"
  panel-light: "#ffffff"
  panel-2-light: "#faf9f6"
  panel-3-light: "#ecebe4"
  border-light: "#e3e0d8"
  border-soft-light: "#ecebe4"
  fg-light: "#15130f"
  text-dim-light: "#5a564d"
  text-mute-light: "#8d877c"
  accent-light: "#4a7ba8"
  destructive-light: "#b94545"
  success-light: "#2f8a5b"
  warning-light: "#a37b1f"

  bg-dark: "#0e0e0f"
  panel-dark: "#141416"
  panel-2-dark: "#1a1a1c"
  panel-3-dark: "#212124"
  border-dark: "#262628"
  border-soft-dark: "#1e1e20"
  fg-dark: "#ececec"
  text-dim-dark: "#8a8a90"
  text-mute-dark: "#56565c"
  accent-dark: "#8ab4d8"
  destructive-dark: "#e05252"
  success-dark: "#4caf7c"
  warning-dark: "#d4a843"
typography:
  display:
    fontFamily: "Geist, system-ui, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Geist, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Geist, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "Geist Mono, ui-monospace, monospace"
    fontSize: "0.625rem"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.12em"
  mono:
    fontFamily: "Geist Mono, ui-monospace, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  sm: "4px"
  md: "8px"
  lg: "12px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "14px"
  lg: "20px"
  xl: "28px"
  xxl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.fg-dark}"
    textColor: "{colors.bg-dark}"
    rounded: "{rounded.md}"
    padding: "8px 14px"
  button-primary-hover:
    backgroundColor: "{colors.accent-dark}"
    textColor: "{colors.bg-dark}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text-dim-dark}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  button-ghost-hover:
    backgroundColor: "{colors.panel-2-dark}"
    textColor: "{colors.fg-dark}"
  chip-mono:
    backgroundColor: "{colors.panel-3-dark}"
    textColor: "{colors.text-dim-dark}"
    rounded: "{rounded.sm}"
    padding: "0 8px"
    height: "26px"
    typography: "{typography.label}"
  card-panel:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.fg-dark}"
    rounded: "{rounded.md}"
    padding: "14px"
  approval-card:
    backgroundColor: "{colors.panel-2-dark}"
    textColor: "{colors.fg-dark}"
    rounded: "{rounded.md}"
    padding: "14px"
  input-text:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.fg-dark}"
    rounded: "{rounded.md}"
    padding: "10px 14px"
---

# Design System: Talky

## 1. Overview

**Creative North Star: "The Quiet Console"**

Talky is a workspace that stays calm and only steps forward when the conversation needs it. The transcript is the room; the chrome is the doorframe. Most pixels are still — typography, neutrals, hairline borders doing the work — so that when something does happen (a voice visualizer breathing, an approval card arriving, a backend switch landing) the eye knows exactly where to go. The design has a point of view: restrained surface, decisive moment.

The system is dark-mode primary because the user lives in a terminal, but light mode is fully equal — warm off-white (`#f6f5f1`), not white-paper bright. There is one accent color per theme, used sparingly: a fog blue (`#8ab4d8` dark / `#4a7ba8` light) that says "now" — it appears on the active voice viz state, the focus ring, the primary action, the approval moment. Nowhere else.

This system explicitly rejects: the Pipecat / voice-ui-kit devtool aesthetic (status pills, metrics tabs, dev-console feel — talky is not a tool for debugging talky); voice-assistant marketing pages (glassy orbs, soft gradients, "powerful AI" copy — the user already chose this); ChatGPT/Claude.ai consumer chat templates (sidebar history as IA, generic bubbles); and observability dashboards (dense data, status pills as load-bearing pattern — talky is a conversation, not a monitor).

**Key Characteristics:**
- Flat with tonal layering — depth comes from three panel steps, never from shadow.
- One accent, used at <10% of any screen.
- Geist + Geist Mono only — no editorial display face, no inherited Newsreader.
- 8px radius across the system. Not 0 (ui26's no-radius dogma was matchina's), not 16 (too soft).
- Voice viz is the only continuously-moving element. Everything else holds still.
- Mobile-real: phone-while-pacing is a documented use case, not a marketing afterthought.

## 2. Colors

A bicameral palette with one accent. Dark is the primary surface; light is the daytime equal. Tonal layering replaces shadow.

### Primary

- **Fog Blue Accent** (`#8ab4d8` dark / `#4a7ba8` light): The single accent color. Active voice state, focus ring, the approval card's primary action, the wordmark hover, link emphasis. Never decorative. If you can remove it from a surface and the meaning is unchanged, it should be removed.

### Neutral

**Dark theme (primary surface):**
- **Ink** (`#0e0e0f`): The room. App background. Nearly black, slightly warm so it doesn't read electronic.
- **Panel** (`#141416`): The conversation card. One step up from background.
- **Panel 2** (`#1a1a1c`): Popovers, the approval card, hovered surfaces. Two steps up.
- **Panel 3** (`#212124`): Tonal chips, mono badges. Three steps up — used as a stand-in for "selected" or "elevated marker" without resorting to shadow.
- **Border** (`#262628`): The default hairline.
- **Border Soft** (`#1e1e20`): Between turns in the transcript, between rows in a list. Quieter divisions.
- **Text** (`#ececec`): Spoken content, agent replies, the user's lines. The reading color.
- **Text Dim** (`#8a8a90`): Labels, metadata, timestamps on hover, idle status text.
- **Text Mute** (`#56565c`): Placeholders, disabled.

**Light theme:**
- **Warm Off-White** (`#f6f5f1`): App background. Deliberately not `#fff`. Tinted toward the same warm cast the ink has.
- **Panel** (`#ffffff`): Conversation surface, popovers.
- **Panel 2** (`#faf9f6`): Muted surfaces, hovered states.
- **Panel 3** (`#ecebe4`): Tonal chips, selected markers.
- **Border** (`#e3e0d8`): Hairline default.
- **Border Soft** (`#ecebe4`): Between-turn divisions.
- **Text** (`#15130f`), **Text Dim** (`#5a564d`), **Text Mute** (`#8d877c`): Same role hierarchy as dark.

### Semantic

- **Success** (`#4caf7c` dark / `#2f8a5b` light): Approved tool result, "connected", successful state changes.
- **Warning** (`#d4a843` dark / `#a37b1f` light): Steer mode active, retryable degradation.
- **Destructive** (`#e05252` dark / `#b94545` light): Denial buttons, errors that stopped the agent, dangerous approval requests by association.

### Named Rules

**The One Accent Rule.** Fog blue is used on ≤10% of any visible surface. Its rarity is its meaning. Two accent surfaces touching each other is a bug.

**The No Pure Black or White Rule.** `#000` and `#fff` are banned. Every neutral is tinted toward the room's warm cast. Pure black on dark would read as a hole; pure white on light would read as paper, and talky is not a printed object.

**The Tonal Step Rule.** Elevation comes from the panel/panel-2/panel-3 ladder, never from shadow. If a surface needs to feel "above" another, raise it one tonal step. If it needs to feel "below", drop it one. Three steps is the entire vocabulary.

## 3. Typography

**Display Font:** Geist (with system-ui fallback)
**Body Font:** Geist (with system-ui fallback)
**Label/Mono Font:** Geist Mono (with ui-monospace fallback)

**Character:** A single sans pairing with a single mono. Geist is technical but warm — geometric enough to feel deliberate, humanist enough to read for thirty minutes without fatigue. Geist Mono is the voice of the machine: timestamps, eyebrows, status badges, command echoes. The two fonts are the entire typographic vocabulary. There is no editorial display face. There is no Newsreader (it was inherited from a sibling project and removed for vertical-alignment issues).

### Hierarchy

- **Display** (500, 1.875rem, line-height 1.1, tracking -0.02em): The wordmark and empty-state title only. Not used on body screens.
- **Headline** (500, 1.125rem, line-height 1.3, tracking -0.01em): Section labels, approval card titles, profile names in the picker. Sentence case.
- **Title** (500, 1rem, line-height 1.4): Tool call summaries, popover headers.
- **Body** (400, 0.9375rem / 15px, line-height 1.55): The transcript reading size. Optimized for a 600px content column. Line length naturally caps near 65–75ch at that width.
- **Label** (Geist Mono 600, 0.625rem / 10px, tracking 0.12em, UPPERCASE): Eyebrows, voice state (`LISTENING` / `THINKING` / `SPEAKING`), chip text, the steer-mode chip. Mono uppercase is talky's signature for machine-state markers.
- **Mono** (Geist Mono 400, 0.8125rem / 13px): Inline code, command echoes, file paths inside tool cards, timestamps on hover.

### Named Rules

**The Mono Marks the Machine Rule.** Mono type is reserved for content that originated from or refers to the machine: timestamps, command strings, file paths, state labels, chip text. Spoken content — what the user said and what the agent said back — is always sans. The split is how the eye distinguishes conversation from instrumentation at a glance.

**The Sentence Case Rule.** Sentence case for headlines, titles, buttons, profile names. UPPERCASE is reserved for the Label role (mono, 10px, 0.12em tracking). Title Case never appears.

**The 65-75ch Rule.** Body text is constrained to a 65–75 character line length. The 600px centered content column delivers this at the body size; do not widen the column without also raising the body size.

## 4. Elevation

Talky is flat. There are no shadows. Depth is conveyed by tonal layering — the three-step panel ladder (`panel` / `panel-2` / `panel-3`) over the background. The voice-ui-kit's default shadow rules are overridden to flat. Popovers, the approval card, hovered surfaces — all step up tonally, not visually.

There is one exception by design: motion. Surfaces don't lift, but they do animate state. The voice visualizer breathes. State labels (`LISTENING` → `THINKING` → `SPEAKING`) crossfade. The approval card slides into the transcript when it arrives. None of these uses shadow.

### Named Rules

**The Flat-By-Default Rule.** `box-shadow` is forbidden anywhere in talky's UI. If a surface needs to read as "elevated", raise it one tonal step. If it needs to read as "interactive on hover", change its background to the next tonal step on hover. Shadow is not in the vocabulary.

**The State-Through-Motion Rule.** When a surface needs to indicate it has just changed (an approval just arrived, a tool just completed, a state just transitioned), the answer is a brief opacity / transform animation easing out — never a shadow appearing. Reduced-motion fallback is the same surface, instantly placed.

## 5. Components

Components are **refined and unhurried**: modest 8px radius, clear states, no decorative flourish, transitions in the 120–200ms range easing out.

### Buttons

- **Shape:** 8px radius (`rounded-md`) across the board. No pill buttons. No square buttons.
- **Primary:** Text-color background (`#ececec` dark / `#15130f` light), inverted text. Padding `8px 14px`. Used for one action per surface — `Connect`, `Allow`, `Send`.
- **Hover:** Background swaps to the accent fog blue. The accent appears only at the moment of intent.
- **Ghost (default for chrome controls):** Transparent background, dim text. Padding `8px 12px`. Hover background is `panel-2`. Used for `Disconnect`, `More`, picker triggers.
- **Destructive (deny):** Destructive color background, white text. Same shape and padding as Primary. Used in the approval card and nowhere else by default.

### Chips

- **Style:** `panel-3` background, dim text, soft border. 8px radius, 26px tall, mono-label typography (10px, tracking 0.12em).
- **Use:** The steer-mode chip in the input area, the voice-state label next to the visualizer, profile-active indicators in the picker.
- **State:** Selected/active state swaps text color to foreground. No background change — the type does the work.

### Cards / Containers

- **Conversation surface:** `panel` background, no border by default. The transcript turns are separated by `border-soft` hairlines, not card edges. Talky's transcript is *not* a stack of cards — it is one flowed surface.
- **Tool cards (inline in transcript):** `panel-2` background, `border` hairline, 8px radius, 14px internal padding. Collapsible — collapsed state is one line summary; expanded reveals output. Distinct visual per kind (shell, file edit, search, fetch) via the leading mono label, not via color.
- **Approval card (signature component):** `panel-2` background, accent-colored border (1px fog blue), 8px radius, 14px internal padding. The verbatim command renders in mono inside a `panel-3` inset. Three buttons across the bottom: Allow (primary), Allow-for-session (ghost), Deny (destructive). This is the moment the design budget is spent on.

### Inputs / Fields

- **Style:** `panel` background, `border` hairline, 8px radius, 10px vertical / 14px horizontal padding, body typography.
- **Focus:** Border swaps to accent fog blue. No glow, no ring beyond the 1px border-color change. The accent appearing IS the focus state.
- **Disabled:** Background drops to `panel-2`, text drops to `text-mute`. No diagonal stripes, no special icons.

### Navigation / Chrome

The header is roomy, not dense. Left: the voice visualizer (larger than transport defaults) with the state label adjacent. Center: profile picker. Right: audio controls, connect/end-call, more-menu. Header height is generous (~64px) — talky is not a power-bar tool.

### Signature: The Voice Visualizer

The single continuously-moving element. It always renders, but communicates state through color and motion amplitude: idle is dim and slow, listening is dim and reactive to mic input, thinking is accent-tinted with rhythmic motion, speaking is accent-saturated and reactive to TTS output. It is the room's heartbeat — the proof that talky is present.

### Signature: The Approval Card

The interrupt moment. Slides into the transcript when an agent requests permission. Carries the verbatim command in mono inside a `panel-3` inset (so the user reads exactly what will run). Three buttons across the bottom, all keyboard-accessible. Voice can answer "allow" / "allow for session" / "deny" too. This card is the visual translation of talky's differentiator and should always feel like the most important thing on screen when it appears.

## 6. Do's and Don'ts

### Do:
- **Do** use the three-step panel ladder (`panel` / `panel-2` / `panel-3`) for elevation. Three steps is the entire vocabulary.
- **Do** keep the accent fog blue (`#8ab4d8` dark / `#4a7ba8` light) at ≤10% of any visible surface.
- **Do** use 8px radius across every component. Not 0, not 16, not "rounded-xl".
- **Do** use Geist Mono uppercase tracked at 0.12em for state labels, eyebrows, and chip text. That's talky's signature.
- **Do** make the voice visualizer the only continuously-moving element on screen.
- **Do** give the Approval Card the design budget — accent border, verbatim command in a mono inset, three keyboard-accessible buttons.
- **Do** test every layout at phone width. Phone-while-pacing is a real use case.
- **Do** respect `prefers-reduced-motion` — degrade the visualizer and state transitions to near-static.

### Don't:
- **Don't** use `box-shadow` anywhere. If a surface needs depth, it goes up a tonal step. Shadow is not in talky's vocabulary.
- **Don't** use `#000` or `#fff`. Every neutral is tinted toward the warm cast.
- **Don't** import Newsreader (or any other editorial display face). Geist + Geist Mono is the entire type system. The Newsreader baseline came from a sibling project (`ui26` / matchina) and has been removed.
- **Don't** style talky to look like Pipecat / voice-ui-kit's defaults — status pills clusters, metrics tabs, transport-debugging chrome. Talky is the product, not a tool for debugging the product.
- **Don't** style talky like a voice-assistant marketing page (Siri, Pi web, Alexa) — glassy orbs, soft radial gradients, glow effects on the visualizer, "powerful AI" framing.
- **Don't** copy ChatGPT or Claude.ai's IA — sidebar of past chats as primary navigation, marketing-flavored empty states, generic message bubbles.
- **Don't** wrap the transcript in cards. The transcript is one flowed surface separated by hairline dividers, not a stack of containers.
- **Don't** use gradient text, glassmorphism, or `border-left` colored stripes as accents. Banned at the system level.
- **Don't** add a second accent. Fog blue is the only accent. Success/warning/destructive are *semantic* colors, not accents — they only appear on state, never decoratively.
- **Don't** use Title Case anywhere. Sentence case for prose, UPPERCASE (mono, tracked) for the Label role. Title Case is the marketing-page tell.
- **Don't** widen the content column past ~600px without raising body size. The 65–75ch line length is load-bearing for transcript readability.
