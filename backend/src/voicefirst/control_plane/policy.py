"""Policy engine: RBAC, tool risk classification, and guardrail factories.

Safety is enforced in code, not just prompts:

* `classify_tool` maps a tool name to `read` / `mutating` / `destructive`.
* `InputGuardrail`s block prompt-injection patterns and forbidden directives.
* `OutputGuardrail`s redact secrets from anything the model emits.
* `ToolGuardrail`s enforce role permissions on tool execution.

Mutating/destructive tools additionally go through human approval via
`requires_approval` (native tools) or `ApprovalRequiredToolset` (MCP toolsets).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai_harness.guardrails import (
    GuardrailResult,
    InputBlocked,
    InputGuardrail,
    OutputGuardrail,
    ToolBlocked,
    ToolGuardrail,
)

ROLE_LEVELS: dict[str, int] = {"viewer": 0, "ops": 1, "admin": 2}

MUTATING_PATTERNS = re.compile(
    r"(create|add|update|edit|modify|post|send|reply|upload|write|open|assign|label|move|clone|fork|star|subscribe|run|deploy|start|restart|invite)",
    re.IGNORECASE,
)
DESTRUCTIVE_PATTERNS = re.compile(
    r"(delete|del_|remove|drop|truncate|force|merge|close|revoke|reset|purge|destroy|terminate|kill|overwrite|replace)",
    re.IGNORECASE,
)

# Prompt-injection heuristics: instructions trying to override platform policy.
INJECTION_PATTERNS = re.compile(
    r"((ignore|disregard|override)\s+(all\s+|any\s+|your\s+|the\s+|previous\s+|prior\s+)*(instructions|rules|policies|guardrails)|"
    r"you are now\s+(an?\s+|the\s+)?(unrestricted|uncensored|dan|jailbroken)|act as\s+(an?\s+|the\s+)?(unrestricted|uncensored)|system prompt:|reveal (your\s+)?(system\s+)?(prompt|instructions)|"
    r"disable (all\s+)?(guardrails|safety|approvals)|bypass (all\s+)?(approvals|permissions|policy))",
    re.IGNORECASE,
)

# Outputs must never leak credentials.
SECRET_PATTERNS = re.compile(
    r"((?:sk|pk|rk)-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|xox[bpars]-[A-Za-z0-9-]{10,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})",
)

# PII redaction (compliance): each pattern redacts to a typed marker so
# operators can still tell *what* was removed. Credit-card candidates are
# Luhn-validated first so ordinary numbers (counts, order ids) survive.
_PII_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PII_PHONE = re.compile(r"(?<![\w./+-])(?:\+?1[-.\s])?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\w)")
_PII_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PII_IP = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_PII_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){12,15}\d\b")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Redact PII from text. Returns (redacted_text, types_found)."""
    found: list[str] = []

    def _sub(marker: str, match: re.Match[str]) -> str:
        if marker not in found:
            found.append(marker)
        return f"[REDACTED-{marker}]"

    text = _PII_EMAIL.sub(lambda m: _sub("EMAIL", m), text)
    text = _PII_SSN.sub(lambda m: _sub("SSN", m), text)
    text = _PII_IP.sub(lambda m: _sub("IP-ADDRESS", m), text)
    text = _PII_PHONE.sub(lambda m: _sub("PHONE", m), text)

    def _card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 16 and _luhn_ok(digits):
            return _sub("CREDIT-CARD", match)
        return match.group(0)

    text = _PII_CARD_CANDIDATE.sub(_card, text)
    return text, found


class Role:
    """Convenience constants + comparisons for RBAC."""

    VIEWER = "viewer"
    OPS = "ops"
    ADMIN = "admin"

    @staticmethod
    def at_least(role: str, minimum: str) -> bool:
        return ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS.get(minimum, 0)


@dataclass(frozen=True)
class ToolRisk:
    name: str
    risk: str  # read | mutating | destructive


def classify_tool(tool_name: str) -> str:
    """Classify a tool call risk by its name. `destructive` implies `mutating`."""
    if DESTRUCTIVE_PATTERNS.search(tool_name):
        return "destructive"
    if MUTATING_PATTERNS.search(tool_name):
        return "mutating"
    return "read"


@dataclass
class PolicyContext:
    """Per-request authorization context attached to agent deps."""

    tenant_id: str
    user_id: str
    role: str = Role.VIEWER
    extra: dict[str, Any] = field(default_factory=dict)

    def can_execute(self, risk: str, mutating_min_role: str) -> bool:
        if risk == "read":
            return True
        minimum = mutating_min_role if risk == "mutating" else Role.ADMIN
        return Role.at_least(self.role, minimum)


class PolicyEngine:
    def __init__(
        self,
        *,
        mutating_min_role: str = Role.OPS,
        require_approval_for_mutations: bool = True,
        redact_pii: bool = False,
    ) -> None:
        self.mutating_min_role = mutating_min_role
        self.require_approval_for_mutations = require_approval_for_mutations
        self.redact_pii = redact_pii

    def requires_approval(self, tool_name: str, role: str) -> bool:
        """Whether a tool call needs a human decision given the caller's role."""
        risk = classify_tool(tool_name)
        if risk == "read":
            return False
        if not Role.at_least(role, self.mutating_min_role):
            return True
        return self.require_approval_for_mutations

    def tool_requires_approval(self, tool_name: str) -> bool:
        """Static default (role unknown at agent-build time) used on Tool definitions."""
        return classify_tool(tool_name) != "read" and self.require_approval_for_mutations

    # -- Guardrail factories -------------------------------------------------

    def input_guardrail(self) -> InputGuardrail:
        """Blocks injection attempts and policy overrides before the model runs."""

        async def guard(prompt: str) -> GuardrailResult:  # type: ignore[no-untyped-def, unused-ignore]
            match = INJECTION_PATTERNS.search(prompt or "")
            if match:
                raise InputBlocked(
                    f"Request blocked by input guardrail: instruction-override pattern detected "
                    f"(matched: {match.group(0)!r})."
                )
            return GuardrailResult.allow()

        return InputGuardrail(guard)

    def output_guardrail(self) -> OutputGuardrail:
        """Redacts credentials (always) and PII (when enabled) from model output."""

        async def guard(output: object) -> GuardrailResult:  # type: ignore[no-untyped-def, unused-ignore]
            text = output if isinstance(output, str) else str(output)
            redacted = SECRET_PATTERNS.sub("[REDACTED]", text)
            if self.redact_pii:
                redacted, _found = redact_pii(redacted)
            if redacted != text:
                return GuardrailResult.replace(redacted)
            return GuardrailResult.allow()

        return OutputGuardrail(guard)

    def tool_guardrail(self) -> ToolGuardrail:
        """Enforces RBAC on every tool execution (defense in depth)."""

        async def guard(ctx: Any, info: Any) -> GuardrailResult:  # type: ignore[no-untyped-def, unused-ignore]
            deps = getattr(ctx, "deps", None)
            policy: PolicyContext | None = getattr(deps, "policy", None)
            if policy is None:
                return GuardrailResult.allow()
            risk = classify_tool(info.name)
            if not policy.can_execute(risk, self.mutating_min_role):
                raise ToolBlocked(
                    f"Role '{policy.role}' is not permitted to execute {risk} tool '{info.name}'."
                )
            return GuardrailResult.allow()

        return ToolGuardrail(guard=guard)
