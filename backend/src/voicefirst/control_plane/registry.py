"""Control-plane registries: agents, skills (SKILL.md), and MCP servers.

Registries are the "Kubernetes for agents" layer: the orchestrator discovers
specialists here instead of importing them, skills are first-class platform
objects backed by SKILL.md files, and MCP servers are configured centrally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai.mcp import (
    FastMCPClient,
    MCPToolset,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from pydantic_ai_skills import Skill, discover_skills
from sqlalchemy import select

from voicefirst.db.models import AgentRecord, MCPServerRecord, SkillRecord, new_id
from voicefirst.db.session import Database


@dataclass
class AgentDescriptor:
    slug: str
    name: str
    description: str
    kind: str = "specialist"  # orchestrator | specialist
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    owner: str = "platform"
    status: str = "enabled"
    capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "skills": self.skills,
            "mcp_servers": self.mcp_servers,
            "version": self.version,
            "owner": self.owner,
            "status": self.status,
            "capabilities": self.capabilities,
        }


class AgentRegistry:
    """Code-defined agent descriptors + optional DB overrides (enable/disable)."""

    def __init__(self, db: Database, descriptors: list[AgentDescriptor]) -> None:
        self._db = db
        self._descriptors = {d.slug: d for d in descriptors}

    async def seed(self) -> None:
        async with self._db.session() as session:
            for descriptor in self._descriptors.values():
                existing = await session.scalar(select(AgentRecord).where(AgentRecord.slug == descriptor.slug))
                if existing:
                    continue
                session.add(
                    AgentRecord(
                        id=new_id("agt"),
                        slug=descriptor.slug,
                        name=descriptor.name,
                        description=descriptor.description,
                        kind=descriptor.kind,
                        skills=descriptor.skills,
                        mcp_servers=descriptor.mcp_servers,
                        status="enabled",
                    )
                )

    async def list_agents(self) -> list[AgentDescriptor]:
        async with self._db.session() as session:
            rows = (await session.execute(select(AgentRecord).order_by(AgentRecord.slug))).scalars().all()
        out: list[AgentDescriptor] = []
        for row in rows:
            base = self._descriptors.get(row.slug)
            out.append(
                AgentDescriptor(
                    slug=row.slug,
                    name=row.name,
                    description=row.description,
                    kind=row.kind,
                    skills=list(row.skills or []),
                    mcp_servers=list(row.mcp_servers or []),
                    version=row.version,
                    owner=row.owner,
                    status=row.status,
                    capabilities=base.capabilities if base else [],
                )
            )
        # Include code-defined descriptors not yet seeded.
        seen = {a.slug for a in out}
        for slug, d in self._descriptors.items():
            if slug not in seen:
                out.append(d)
        return sorted(out, key=lambda a: (a.kind != "orchestrator", a.slug))

    async def get(self, slug: str) -> AgentDescriptor | None:
        for agent in await self.list_agents():
            if agent.slug == slug:
                return agent
        return None

    async def set_status(self, slug: str, status: str) -> AgentDescriptor | None:
        async with self._db.session() as session:
            row = await session.scalar(select(AgentRecord).where(AgentRecord.slug == slug))
            if row is None:
                return None
            row.status = status
            updated = row
        for d in self._descriptors.values():
            if d.slug == slug:
                d.status = updated.status
        return await self.get(slug)


class SkillRegistry:
    """Scans the skills directory for SKILL.md files (pydantic-ai-skills)."""

    def __init__(self, db: Database, skills_dir: Path) -> None:
        self._db = db
        self._skills_dir = skills_dir

    def discover(self) -> list[Skill]:
        if not self._skills_dir.exists():
            return []
        return discover_skills(self._skills_dir, validate=False)

    def to_record(self, skill: Skill) -> SkillRecord:
        return SkillRecord(
            id=new_id("skl"),
            slug=skill.name,
            name=skill.name,
            description=skill.description or "",
            path=str(skill.uri or ""),
        )

    async def list_skills(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for skill in self.discover():
            out.append(
                {
                    "slug": skill.name,
                    "name": skill.name,
                    "description": skill.description or "",
                    "path": str(skill.uri or ""),
                    "status": "enabled",
                    "metadata": skill.metadata or {},
                }
            )
        return out

    def skill_dir(self, slug: str) -> Path | None:
        """Directory containing a given skill's SKILL.md (for SkillsCapability)."""
        candidate = self._skills_dir / slug
        if (candidate / "SKILL.md").exists():
            return candidate
        return None


class MCPRegistry:
    """MCP server registry: central configuration, one toolset per server."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_servers(self) -> list[MCPServerRecord]:
        async with self._db.session() as session:
            rows = (await session.execute(select(MCPServerRecord).order_by(MCPServerRecord.slug))).scalars().all()
        return list(rows)

    async def upsert(
        self,
        *,
        slug: str,
        name: str,
        transport: str = "streamable-http",
        url: str | None = None,
        command: str | None = None,
        headers: dict[str, str] | None = None,
        status: str = "enabled",
    ) -> MCPServerRecord:
        async with self._db.session() as session:
            row = await session.scalar(select(MCPServerRecord).where(MCPServerRecord.slug == slug))
            if row is None:
                row = MCPServerRecord(id=new_id("mcp"), slug=slug, name=name)
                session.add(row)
            row.transport = transport
            row.url = url
            row.command = command
            row.headers = headers
            row.status = status
            row.name = name
        return row

    def build_toolset(self, record: MCPServerRecord) -> MCPToolset | None:
        """Build a pydantic-ai MCPToolset for a registered server, if configured."""
        if record.status != "enabled":
            return None
        if record.transport in ("streamable-http", "sse") and record.url:
            transport: StreamableHttpTransport | SSETransport
            if record.transport == "sse":
                transport = SSETransport(url=record.url, headers=dict(record.headers or {}))
            else:
                transport = StreamableHttpTransport(url=record.url, headers=dict(record.headers or {}))
            return MCPToolset(FastMCPClient(transport))
        if record.transport == "stdio" and record.command:
            parts = record.command.split()
            transport = StdioTransport(parts[0], parts[1:], env=dict(record.headers or {}) or None)
            return MCPToolset(FastMCPClient(transport))
        return None

    async def toolsets_for(self, slugs: list[str]) -> list[MCPToolset]:
        """Resolve toolsets for the MCP servers a given agent is configured with."""
        toolsets: list[MCPToolset] = []
        for record in await self.list_servers():
            if record.slug in slugs:
                toolset = self.build_toolset(record)
                if toolset is not None:
                    toolsets.append(toolset)
        return toolsets

    async def set_status(self, slug: str, status: str) -> MCPServerRecord | None:
        async with self._db.session() as session:
            row = await session.scalar(select(MCPServerRecord).where(MCPServerRecord.slug == slug))
            if row is None:
                return None
            row.status = status
            updated = row
        return updated

    async def seed_from_settings(self, settings: Any) -> None:
        """Seed well-known MCP servers from settings/env. Existing rows keep user edits."""
        specs: list[tuple[str, str, str | None]] = [
            ("github", "GitHub MCP", settings.github_mcp_url),
            ("slack", "Slack MCP", settings.slack_mcp_url),
            ("mongodb", "MongoDB MCP", settings.mongodb_mcp_url),
        ]
        for slug, name, url in specs:
            if not url:
                continue
            async with self._db.session() as session:
                existing = await session.scalar(select(MCPServerRecord).where(MCPServerRecord.slug == slug))
                if existing is not None:
                    continue  # keep whatever the operator configured
                session.add(
                    MCPServerRecord(
                        id=new_id("mcp"),
                        slug=slug,
                        name=name,
                        transport="streamable-http",
                        url=url,
                        status="enabled",
                    )
                )

    async def get(self, slug: str) -> MCPServerRecord | None:
        async with self._db.session() as session:
            return await session.scalar(select(MCPServerRecord).where(MCPServerRecord.slug == slug))

    async def set_health(self, slug: str, health: str) -> None:
        from datetime import UTC, datetime

        async with self._db.session() as session:
            row = await session.scalar(select(MCPServerRecord).where(MCPServerRecord.slug == slug))
            if row is not None:
                row.health = health
                row.last_checked_at = datetime.now(UTC)
