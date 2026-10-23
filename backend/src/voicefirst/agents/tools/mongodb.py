"""MongoDB tools.

With `MONGODB_URI` set, real queries run through the async pymongo driver.
Without a URI, the tools operate on a seeded in-memory demo collection so the
MongoDB specialist is fully demonstrable offline (results are always labeled
with their `source`).

Production behavior:
* Live aggregation runs server-side (`$group` pipeline) — never a truncated
  client-side scan, so MRR totals are correct regardless of collection size.
* Documents are JSON-safe: every `_id` (ObjectId included) is stringified
  before it can reach the model or the audit trail.
* Mutations in live mode go through the driver with an audit record; demo
  mutations are clearly labeled `source: demo`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import RunContext, Tool

from voicefirst.agents.deps import AgentDeps

CRM_DATABASE = "crm"
CUSTOMERS_COLLECTION = "customers"

# --- In-memory demo store (used when MONGODB_URI is absent) -----------------

_DEMO_CUSTOMERS: list[dict[str, Any]] = [
    {"_id": "c-1001", "name": "Northwind Traders", "plan": "enterprise", "mrr": 4800, "region": "emea", "churn_risk": 0.12},
    {"_id": "c-1002", "name": "Acme Robotics", "plan": "growth", "mrr": 1900, "region": "amer", "churn_risk": 0.31},
    {"_id": "c-1003", "name": "Globex Foods", "plan": "growth", "mrr": 2200, "region": "emea", "churn_risk": 0.08},
    {"_id": "c-1004", "name": "Initech Systems", "plan": "starter", "mrr": 400, "region": "apac", "churn_risk": 0.55},
    {"_id": "c-1005", "name": "Umbrella Health", "plan": "enterprise", "mrr": 5100, "region": "amer", "churn_risk": 0.05},
]


def _mongo_uri(ctx: RunContext[AgentDeps] | None = None) -> str | None:
    explicit = os.getenv("VOICEFIRST_MONGODB_URI")
    if explicit:
        return explicit
    if ctx is not None and ctx.deps.runtime is not None:
        return getattr(ctx.deps.runtime.settings, "mongodb_uri", None)
    return os.getenv("MONGODB_URI")


def _json_safe(doc: dict[str, Any]) -> dict[str, Any]:
    """Make a Mongo document JSON-safe: ObjectIds (and datetimes) → strings."""
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id" or isinstance(value, (bytes,)):
            out[key] = str(value)
        elif isinstance(value, dict):
            out[key] = _json_safe(value)
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for v in value]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        else:
            out[key] = str(value)
    return out


async def inspect_schema(ctx: RunContext[AgentDeps], database: str = CRM_DATABASE) -> dict[str, Any]:
    """Describe collections and their field shape. Read-only."""
    uri = _mongo_uri(ctx)
    if uri:
        try:
            from pymongo import AsyncMongoClient  # pymongo>=4.10 has async support

            client = AsyncMongoClient(uri)
            try:
                collections = await client[database].list_collection_names()
                sample: dict[str, list[str]] = {}
                for name in collections[:10]:
                    doc = await client[database][name].find_one()
                    sample[name] = sorted(doc.keys()) if doc else []
                return {"database": database, "collections": collections, "fields": sample, "source": "live"}
            finally:
                await client.close()
        except Exception as exc:  # noqa: BLE001 - fall through to demo
            return {
                "database": database,
                "source": "demo (live failed)",
                "error": str(exc)[:200],
                "collections": [CUSTOMERS_COLLECTION],
                "fields": {CUSTOMERS_COLLECTION: sorted(_DEMO_CUSTOMERS[0].keys())},
            }
    return {
        "database": database,
        "source": "demo",
        "collections": [CUSTOMERS_COLLECTION],
        "fields": {CUSTOMERS_COLLECTION: sorted(_DEMO_CUSTOMERS[0].keys())},
    }


async def find_customers(
    ctx: RunContext[AgentDeps],
    plan: str | None = None,
    region: str | None = None,
    min_mrr: int | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Query the customers collection with simple filters. Read-only."""
    limit = max(1, min(int(limit), 50))
    uri = _mongo_uri(ctx)
    if uri:
        try:
            from pymongo import AsyncMongoClient

            query: dict[str, Any] = {}
            if plan:
                query["plan"] = plan
            if region:
                query["region"] = region
            if min_mrr is not None:
                query["mrr"] = {"$gte": int(min_mrr)}
            client = AsyncMongoClient(uri)
            try:
                cursor = client[CRM_DATABASE][CUSTOMERS_COLLECTION].find(query).limit(limit)
                return [_json_safe(d) async for d in cursor]
            finally:
                await client.close()
        except Exception as exc:  # noqa: BLE001
            return [{"error": f"live query failed: {str(exc)[:200]}", "source": "demo"}]
    rows = _DEMO_CUSTOMERS
    if plan:
        rows = [r for r in rows if r["plan"] == plan]
    if region:
        rows = [r for r in rows if r["region"] == region]
    if min_mrr is not None:
        rows = [r for r in rows if r["mrr"] >= min_mrr]
    return [dict(r) for r in rows[:limit]]


async def aggregate_mrr(ctx: RunContext[AgentDeps], group_by: str = "region") -> dict[str, Any]:
    """Aggregate MRR by a dimension (region or plan) over ALL customers.
    Read-only. Live mode aggregates server-side, so totals cover the whole
    collection rather than a single page of results."""
    if group_by not in ("region", "plan"):
        return {"error": "invalid_group_by", "detail": "group_by must be 'region' or 'plan'."}
    uri = _mongo_uri(ctx)
    if uri:
        try:
            from pymongo import AsyncMongoClient

            pipeline = [
                {"$group": {"_id": f"${group_by}", "mrr": {"$sum": "$mrr"}, "customers": {"$sum": 1}}},
                {"$sort": {"mrr": -1}},
            ]
            client = AsyncMongoClient(uri)
            try:
                groups_raw = await client[CRM_DATABASE][CUSTOMERS_COLLECTION].aggregate(pipeline).to_list(length=100)
                total = float(sum(g.get("mrr") or 0 for g in groups_raw))
                groups = {
                    str(g["_id"]): {"mrr": float(g.get("mrr") or 0), "customers": int(g.get("customers") or 0)}
                    for g in groups_raw
                }
                return {
                    "group_by": group_by,
                    "total_mrr": total,
                    "groups": groups,
                    "as_of": datetime.now(UTC).isoformat(),
                    "source": "live",
                }
            finally:
                await client.close()
        except Exception as exc:  # noqa: BLE001
            return {"error": f"live aggregation failed: {str(exc)[:200]}"}

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in _DEMO_CUSTOMERS:
        key = str(row.get(group_by, "unknown"))
        totals[key] = totals.get(key, 0) + float(row.get("mrr", 0))
        counts[key] = counts.get(key, 0) + 1
    return {
        "group_by": group_by,
        "total_mrr": sum(totals.values()),
        "groups": {k: {"mrr": v, "customers": counts[k]} for k, v in sorted(totals.items())},
        "as_of": datetime.now(UTC).isoformat(),
        "source": "demo",
    }


async def update_customer_plan(ctx: RunContext[AgentDeps], customer_id: str, new_plan: str) -> dict[str, Any]:
    """Change a customer's plan. Mutating — requires human approval."""
    customer_id = (customer_id or "").strip()
    new_plan = (new_plan or "").strip()
    if not customer_id or not new_plan:
        return {"updated": False, "error": "missing_args", "detail": "customer_id and new_plan are required."}
    uri = _mongo_uri(ctx)
    if ctx.deps.runtime is not None:
        await ctx.deps.runtime.audit.record(
            actor=ctx.deps.user_id,
            action="mongodb.customer_plan_update",
            resource=f"customer:{customer_id}",
            tenant_id=ctx.deps.tenant_id,
            detail={"new_plan": new_plan, "mode": "live" if uri else "demo"},
        )
    if uri:
        try:
            from pymongo import AsyncMongoClient, ReturnDocument

            client = AsyncMongoClient(uri)
            try:
                updated = await client[CRM_DATABASE][CUSTOMERS_COLLECTION].find_one_and_update(
                    {"$or": [{"_id": customer_id}, {"name": customer_id}]},
                    {"$set": {"plan": new_plan}},
                    return_document=ReturnDocument.AFTER,
                )
            finally:
                await client.close()
            if updated is None:
                return {"updated": False, "error": "not_found", "detail": f"No customer matches '{customer_id}'."}
            return {"updated": True, "customer": _json_safe(updated), "source": "live"}
        except Exception as exc:  # noqa: BLE001
            return {"updated": False, "error": f"live update failed: {str(exc)[:200]}"}

    # Demo-mode mutation of the in-memory store.
    for row in _DEMO_CUSTOMERS:
        if row["_id"] == customer_id:
            row["plan"] = new_plan
            return {"updated": True, "customer": dict(row), "source": "demo"}
    return {"updated": False, "error": "not_found", "detail": f"customer {customer_id} not found (demo store)"}


def mongodb_tools(approval_default: bool = True) -> list[Tool]:
    return [
        Tool(inspect_schema, name="mongodb_inspect_schema", takes_ctx=True),
        Tool(find_customers, name="mongodb_find_customers", takes_ctx=True),
        Tool(aggregate_mrr, name="mongodb_aggregate_mrr", takes_ctx=True),
        Tool(
            update_customer_plan,
            name="mongodb_update_customer_plan",
            takes_ctx=True,
            requires_approval=approval_default,
            max_retries=1,
        ),
    ]
