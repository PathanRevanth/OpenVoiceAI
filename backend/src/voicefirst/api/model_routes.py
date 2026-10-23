"""Live model catalog for the composer picker: GET /api/models/catalog.

Each provider is queried with the caller's own key (saved connection, else the
platform key). Results are cached briefly per key, so a model a provider retires
disappears from this list shortly after the provider stops serving it.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request

from voicefirst.control_plane.auth import Identity, require_identity
from voicefirst.control_plane.model_catalog import CATALOG, FETCHERS

router = APIRouter()


@router.get("/api/models/catalog", tags=["chat"])
async def model_catalog(
    request: Request,
    refresh: bool = Query(default=False),
    identity: Identity = Depends(require_identity),
) -> dict:
    runtime = request.app.state.runtime
    user_id = identity.policy.user_id
    providers = list(FETCHERS.keys())
    keys = await asyncio.gather(*(runtime.connections.key_for_provider(user_id, p) for p in providers))
    results = await asyncio.gather(
        *(CATALOG.provider_catalog(p, key, refresh=refresh) for p, key in zip(providers, keys))
    )
    return {"providers": {r.provider: r.to_dict() for r in results}}
