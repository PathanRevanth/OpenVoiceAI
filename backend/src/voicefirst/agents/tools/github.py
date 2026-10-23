"""GitHub tools.

Built-in tools use the public REST API via httpx — token optional for public
repos — so the specialist works with zero configuration. When
`GITHUB_TOKEN`/OAuth (+ optional `GITHUB_MCP_URL`) is configured, the agent
additionally prefers the registered GitHub MCP server.

Production behavior:
* Repos are validated as `owner/name` before any URL is built.
* Provider errors never leak raw tracebacks into the model: recoverable
  failures (rate limits, 5xx) raise `ModelRetry` so the model can wait/retry;
  permanent failures (missing token, 404) return structured error dicts the
  model can explain to the user.
* Result sizes are clamped (limits, body lengths) to protect model context.

Mutating tools are marked `requires_approval=True` so the control plane routes
them through the human-approval flow.
"""

from __future__ import annotations

import os
import re
from typing import Any

from pydantic_ai import ModelRetry, RunContext, Tool

from voicefirst.agents.deps import AgentDeps
from voicefirst.agents.tools import _http

GITHUB_API = "https://api.github.com"

_REPO_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _token(ctx: RunContext[AgentDeps] | None = None) -> str | None:
    explicit = os.getenv("VOICEFIRST_GITHUB_TOKEN")
    if explicit:
        return explicit
    if ctx is not None:
        runtime = ctx.deps.runtime
        if runtime is not None:
            return getattr(runtime.settings, "github_token", None)
    return os.getenv("GITHUB_TOKEN")


def _validate_repo(repo: str) -> str | None:
    """Return an error message when `repo` is not `owner/name`, else None.

    Each segment must start alphanumeric (GitHub rule) — this also rejects
    path-traversal shapes like `../etc` and empty segments."""
    parts = repo.strip().split("/")
    if len(parts) != 2 or not all(_REPO_PART_RE.match(p) for p in parts):
        return f"'{repo}' is not a valid repository reference — use the form 'owner/name'."
    return None


async def _request(
    method: str,
    path: str,
    *,
    token: str | None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    """One governed GitHub API call. Raises ModelRetry for transient failures
    and returns parsed JSON otherwise; HTTP errors become ModelRetry with a
    human-actionable message (the model may correct arguments and retry)."""
    async with _http.make_client(timeout=20.0) as client:
        resp = await client.request(
            method,
            f"{GITHUB_API}{path}",
            params=params,
            json=json_body,
            headers=_headers(token),
        )
    if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
        reset = resp.headers.get("X-RateLimit-Reset", "")
        raise ModelRetry(
            "GitHub API rate limit exhausted"
            + (f" (resets at epoch {reset})" if reset else "")
            + ". Wait before retrying, or configure a GITHUB_TOKEN for a higher limit."
        )
    if resp.status_code >= 500:
        raise ModelRetry(f"GitHub returned a server error ({resp.status_code}). Retry the same call.")
    if resp.status_code == 404:
        return {"error": "not_found", "detail": f"GitHub resource '{path}' does not exist (404)."}
    if resp.status_code in (401, 403):
        return {
            "error": "forbidden",
            "detail": "GitHub rejected the credentials — the token may be missing, expired, or lacking scopes.",
        }
    if resp.status_code == 422:
        detail = ""
        try:
            message = resp.json().get("message", "")
            detail = f" {message}"
        except Exception:  # noqa: BLE001
            pass
        return {"error": "validation_failed", "detail": f"GitHub rejected the query.{detail}"}
    if resp.status_code >= 400:
        return {"error": f"http_{resp.status_code}", "detail": resp.text[:200]}
    return resp.json()


async def search_repositories(query: str, limit: int = 5) -> dict[str, Any]:
    """Search public GitHub repositories by keyword. Read-only."""
    query = (query or "").strip()
    if not query:
        return {"error": "empty_query", "detail": "Provide a search keyword, e.g. 'pydantic ai'."}
    limit = max(1, min(int(limit), 10))
    data = await _request(
        "GET", "/search/repositories", token=_token(), params={"q": query, "per_page": limit}
    )
    if isinstance(data, dict) and "error" in data:
        return data
    items = [
        {
            "full_name": item["full_name"],
            "description": (item.get("description") or "")[:200],
            "stars": item.get("stargazers_count"),
            "url": item.get("html_url"),
            "language": item.get("language"),
        }
        for item in data.get("items", [])
    ]
    return {"total": data.get("total_count"), "items": items}


async def get_pull_request(repo: str, number: int) -> dict[str, Any]:
    """Fetch a pull request (repo as owner/name). Read-only."""
    if err := _validate_repo(repo):
        return {"error": "invalid_repo", "detail": err}
    data = await _request("GET", f"/repos/{repo}/pulls/{int(number)}", token=_token())
    if isinstance(data, dict) and "error" in data:
        return data
    return {
        "title": data.get("title"),
        "state": data.get("state"),
        "user": (data.get("user") or {}).get("login"),
        "body": (data.get("body") or "")[:2000],
        "additions": data.get("additions"),
        "deletions": data.get("deletions"),
        "changed_files": data.get("changed_files"),
        "merged": data.get("merged"),
        "url": data.get("html_url"),
    }


async def list_issues(repo: str, state: str = "open", limit: int = 10) -> list[dict[str, Any]]:
    """List issues for a repository. Read-only."""
    if err := _validate_repo(repo):
        return [{"error": "invalid_repo", "detail": err}]
    state = state if state in ("open", "closed", "all") else "open"
    limit = max(1, min(int(limit), 30))
    issues = await _request(
        "GET", f"/repos/{repo}/issues", token=_token(), params={"state": state, "per_page": limit}
    )
    if isinstance(issues, dict) and "error" in issues:
        return [issues]
    return [
        {
            "number": i["number"],
            "title": (i.get("title") or "")[:160],
            "state": i.get("state"),
            "url": i.get("html_url"),
        }
        for i in issues
        if "pull_request" not in i
    ]


async def get_commit_activity(repo: str, limit: int = 10) -> list[dict[str, Any]]:
    """Recent commits on the default branch. Read-only."""
    if err := _validate_repo(repo):
        return [{"error": "invalid_repo", "detail": err}]
    limit = max(1, min(int(limit), 30))
    commits = await _request(
        "GET", f"/repos/{repo}/commits", token=_token(), params={"per_page": limit}
    )
    if isinstance(commits, dict) and "error" in commits:
        return [commits]
    return [
        {
            "sha": c["sha"][:8],
            "message": ((c.get("commit") or {}).get("message") or "")[:140],
            "author": ((c.get("commit") or {}).get("author") or {}).get("name"),
            "date": ((c.get("commit") or {}).get("author") or {}).get("date"),
        }
        for c in commits
    ]


async def create_issue_comment(ctx: RunContext[AgentDeps], repo: str, number: int, body: str) -> dict[str, Any]:
    """Comment on an issue or PR. Mutating — requires human approval."""
    if err := _validate_repo(repo):
        return {"error": "invalid_repo", "detail": err}
    token = _token(ctx)
    if not token:
        return {"error": "not_configured", "detail": "GITHUB_TOKEN not configured; cannot comment."}
    if not body or not body.strip():
        return {"error": "empty_body", "detail": "Comment body is empty."}
    body = body[:20_000]  # GitHub hard-caps comments well above this; keep context safe.
    created = await _request(
        "POST",
        f"/repos/{repo}/issues/{int(number)}/comments",
        token=token,
        json_body={"body": body},
    )
    if isinstance(created, dict) and "error" in created:
        return created
    if ctx.deps.runtime is not None:
        await ctx.deps.runtime.audit.record(
            actor=ctx.deps.user_id,
            action="github.comment_created",
            resource=f"{repo}#{number}",
            tenant_id=ctx.deps.tenant_id,
            detail={"length": len(body)},
        )
    return {"url": created.get("html_url"), "created": True}


def github_tools(approval_default: bool = True) -> list[Tool]:
    """Build the GitHub toolset; mutating tools require approval by default."""
    return [
        Tool(search_repositories, name="github_search_repositories", max_retries=1),
        Tool(get_pull_request, name="github_get_pull_request", max_retries=1),
        Tool(list_issues, name="github_list_issues", max_retries=1),
        Tool(get_commit_activity, name="github_get_commit_activity", max_retries=1),
        Tool(
            create_issue_comment,
            name="github_create_issue_comment",
            takes_ctx=True,
            requires_approval=approval_default,
            max_retries=1,
        ),
    ]
