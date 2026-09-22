"""A member departs (memory v7 WP8, design §governance): their person scope is deleted in both tiers,
every fact they lifted to a shared scope keeps a provenance pointer ("from a departed member,
endorsed by …") so the team's knowledge survives while its source is gone, and a certificate — the
counts, the pointers kept, who issued it, a hash — is returned and ledgered."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from ..core.scope import Scope, ScopeRef
from ..db.models import Node
from ..graph import get_driver
from ..ingest.envelope import Actor, EpisodeEnvelope
from ..repositories.episodes import insert_episode
from .direct import DirectMemory


async def depart(session, *, org_id: str | None, user_id: str, issued_by: str | None, reason: str | None) -> dict:
    ref = ScopeRef(Scope.USER, user_id)
    lifted = (await session.execute(select(Node).where(Node.org_id == org_id, Node.scope != "user"))).scalars().all()
    kept = 0
    for n in lifted:
        p = n.props if isinstance(n.props, dict) else {}
        if p.get("published_by") == user_id or p.get("actor") == user_id:
            props = dict(p)
            props["provenance"] = {"from": "departed member", "departed_at": datetime.now(UTC).isoformat(),
                                   "endorsed_by": list(props.get("endorsed_by") or []), "lifted_by": "member"}
            props.pop("published_by", None)
            n.props = props
            flag_modified(n, "props")
            kept += 1
    nodes, edges = await get_driver().purge_scope(session, tenant_id=org_id, scope=ref)
    docs, chunks = await DirectMemory().purge_scope(session, tenant_id=org_id, scope=ref)
    cert = {"schema": "memory/departure-certificate-v1", "org": org_id, "user_id": user_id, "issued_at": datetime.now(UTC).isoformat(),
            "issued_by": issued_by, "reason": reason, "purged": {"nodes": nodes, "edges": edges, "documents": docs, "chunks": chunks},
            "lifted_facts_kept_with_pointer": kept}
    cert["sha256"] = hashlib.sha256(json.dumps(cert, sort_keys=True).encode()).hexdigest()
    env = EpisodeEnvelope(org_id=org_id, scope="org", scope_id=org_id, actor=Actor(user_id=issued_by) if issued_by else Actor(),
                          source="governance", action_type="memory.departed", payload=cert)
    await insert_episode(session, env.to_episode_kwargs(capture_content=False))
    await session.flush()
    return cert
