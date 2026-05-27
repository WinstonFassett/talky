# Product

## Register

product

## Users

One person: the user themself. A power user comfortable in a terminal, usually reading transcripts at a desk in dark mode, sometimes glancing at a phone while pacing during a long-running task. Single user, single machine, no auth, no multi-tenancy. They are running their own daemon and talking to their own agents — not a customer of a service.

Their job-to-be-done is to have a useful spoken conversation with an AI agent that can also act: run shell commands, edit files, call tools. The transcript is the receipt of that conversation. They are not browsing chats, not managing prompts, not consuming content — they are mid-task.

## Product Purpose

Talky is a voice-first AI assistant client that connects a browser to a local daemon over WebRTC + SSE. It exists so the user can talk to any of several agent backends (Claude Code, Pi, openclaw, hermes, opencode) and have the agent both talk back and take action under supervision.

The differentiator is the **approval loop**: when the agent wants to run something with real-world consequences, talky surfaces a card with the verbatim command, and the user can answer Allow / Allow-for-session / Deny by voice or click. No other voice agent does this cleanly.

Success looks like: the user can hold a sustained voice conversation with any backend, watch tool calls and approvals scroll past as a legible record, intervene when needed, and never feel like they're staring at a dev console.

## Brand Personality

**Quiet, opinionated, alive.** Restrained on the surface — most pixels are calm — but the design has a point of view, and it breathes at the moments that matter (voice visualization, the approval interrupt, state transitions). Talky has taste; it is not a neutral canvas.

Tone: present, attentive, unhurried. The UI behaves like someone who's actually listening — it doesn't fidget, it doesn't decorate, it doesn't demand attention until the conversation requires it. When it does speak up — an approval, an error, a state change — that moment reads clearly without shouting.

## Anti-references

- **Pipecat / voice-ui-kit devtool aesthetic.** The current baseline reads as a transport-debugging console: status pills everywhere, metrics tabs, dev-console energy. Talky is not a tool for debugging talky; it is the product.
- **Voice assistant marketing pages.** Siri, Pi web, Alexa-style glassy orbs, soft gradients, friendly-mascot warmth, "powerful AI assistant" copy. Talky is not selling a voice assistant; the user already chose it.
- **ChatGPT / Claude.ai consumer chat templates.** Sidebar of past chats as the primary IA, marketing-flavored empty states, generic bubbles. Talky is one live conversation, not a chat archive product.
- **Observability dashboards.** Dark blue everywhere, dense data, status pills as a load-bearing pattern. Talky is a conversation, not a monitor.

## Design Principles

1. **Backend-agnostic transparency.** Whichever brain is driving — Claude, Pi, openclaw, hermes — its thinking, tool calls, and state are equally legible. Renderers key on part kind (thinking / tool / approval / text / error), never on a specific backend's event shape. No backend gets visual privilege; none gets hidden.
2. **The transcript is the product.** Almost all surface area belongs to the conversation. Chrome (header, input, pickers) exists to serve the transcript, not compete with it. If a feature can't justify pixels above or below the conversation, it doesn't get them.
3. **Calm by default; alive at the moment.** The default state is quiet — small motion, restrained color, no demands. The voice visualizer breathing, the approval card arriving, the state shifting from LISTENING to THINKING — those moments are where the design uses its budget. Everything else stays still so those moments can land.
4. **Voice can carry it; visuals carry what voice can't.** Prose, status, acknowledgments — voice handles. Code, diffs, file lists, command approvals, long-running progress — visuals handle, because voice can't read a shell command aloud and have it survive. The split is the design discipline.
5. **No talky-the-tool, only talky-the-product.** The user is not here to inspect the pipeline. Pipeline state belongs in logs and devtools, not on screen. If the UI ever feels like it's exposing the plumbing, the plumbing is showing through and needs to be hidden.

## Accessibility & Inclusion

- **WCAG AA** minimum on contrast, focus visibility, and target sizes.
- **`prefers-reduced-motion`** respected — the voice visualizer and any state-change motion degrade to static or near-static. Motion is decorative, never load-bearing.
- **Keyboard-complete.** Every voice-equivalent control reachable and operable without a pointer, including approval cards, profile switching, and barge-in. Voice is the primary input, but the keyboard is the always-available fallback.
- **Mobile-real.** Phone-while-pacing is a documented use case, not a marketing afterthought. Layout must hold up at small widths; touch targets meet AA.
