# PRODUCT.md — Voice-First AI

## What it is
A voice-first agentic control plane: one orchestrator agent routes work to specialist
agents (GitHub, Slack, MongoDB, Research) over MCP, with human approval gates on every
mutating action, per-user LLM key vaulting, and full observability (span waterfalls,
usage, audit).

## Audience
Engineering and ops teams running AI agents in production; evaluators assessing agent
platforms. Users are technical, task-focused, and session-long (operations, not browsing).

## Visitor mode
Operate. The user completes tasks: chat with agents, approve mutations, inspect traces,
manage keys and policies. Scanability, consistency, and familiar affordances outrank
expression. The tool disappears into the task.

## Surfaces
- Assistant (chat + voice, streaming, approvals inline)
- Observability (run traces, span waterfalls, filters)
- Agents (fleet data table, per-agent API/webhook access)
- Triggers (scheduled runs)
- LLM Connections (per-user provider key vault)
- Approvals (human-in-the-loop queue)
- Usage & spend
- Audit log
- Settings (policies, models, MCP registry, tokens)

## Brief commitments (user-pinned)
- Glass white light theme + full dark theme; cool neutrals only (no warm/cream/orange cast)
- Indigo accent, restrained: primary actions, selection, state only
- Waveform motif for voice (no microphone glyph)
- Geist family for text and data; no second display family in product UI
- Outfit/Inter/DM Sans class display faces are out of bounds
- Density permitted; hero-metric template and eyebrow labels are out
