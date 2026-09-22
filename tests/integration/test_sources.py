"""memory v7 WP4 — sources: a cold files source answers on the second pass (promote on demand), the
salience floor and the daily budget decline documents with a reason, and an old database is read
through a tool while memory learns its map."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mlpal_memory_graph.core.config import get_settings
from mlpal_memory_graph.db.models import Episode, SourceItem

H = {"X-Test-Org-Id": "orgS", "X-Test-User-Id": "dana", "X-Test-Permissions": "memory.read,memory.write"}


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "share"
    (root / "policies").mkdir(parents=True)
    for i in range(6):
        f = root / "policies" / f"holiday_calendar_{2019 + i}.md"
        f.write_text(f"# Holiday calendar {2019 + i}\n\nOffice closures and bank holidays for the year {2019 + i}. " * 20)
    f = root / "policies" / "merchant_settlement_runbook.md"
    f.write_text("# Merchant settlement runbook\n\nSettlement batches for merchants run nightly at 02:00 UTC. A failed settlement batch is retried "
                 "three times, then paged to the on-call. The reconciler compares the batch ledger with the acquirer file.\n" * 3)
    monkeypatch.setattr(get_settings(), "sources_root", str(tmp_path))
    return root


@pytest.mark.asyncio
async def test_cold_source_answers_on_the_second_pass(client, session, corpus):
    r = await client.post("/api/v1/sources", json={"name": "share", "kind": "files", "root": str(corpus), "workspace": "data"}, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "cold" and r.json()["item_count"] == 7
    # nothing was copied: no chunks yet
    r = await client.get("/api/v1/memory/search", params={"q": "settlement batch retried paged on-call"}, headers=H)
    assert r.json()["passages"] == []
    # the question promotes the matching item and answers from it
    r = await client.get("/api/v1/memory/answer", params={"q": "when do merchant settlement batches run and what happens when one fails"}, headers=H)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["promoted"] == ["share://policies/merchant_settlement_runbook.md"], a["promoted"]
    assert "02:00 UTC" in a["markdown"] and a["passages"] >= 1, a["markdown"][:300]
    items = (await session.execute(select(SourceItem).where(SourceItem.admitted.is_(True)))).scalars().all()
    assert [i.ref for i in items] == ["policies/merchant_settlement_runbook.md"] and items[0].admitted_by == "question"
    # asked again: memory answers, nothing more is promoted
    r = await client.get("/api/v1/memory/answer", params={"q": "when do merchant settlement batches run"}, headers=H)
    assert r.json()["promoted"] == [] and "02:00 UTC" in r.json()["markdown"]
    srcs = (await client.get("/api/v1/sources", headers=H)).json()["sources"]
    assert srcs[0]["status"] == "warm"


@pytest.mark.asyncio
async def test_salience_floor_and_budget_decline_with_a_reason(client, session):
    admin = {**H, "X-Test-Permissions": "*"}   # governing the org's policy is an admin act
    r = await client.put("/api/v1/memory/policy", json={"scope": "org", "scope_id": "orgS", "min_salience": 0.6, "source_budget_per_day": {"bulk": 2}}, headers=admin)
    assert r.status_code == 200, r.text
    fresh = datetime.now(UTC).isoformat()
    stale = (datetime.now(UTC) - timedelta(days=1500)).isoformat()
    body = lambda t, c, s="bulk", v=fresh: {"title": t, "content": c, "source": s, "valid_at": v, "scope": "org"}  # noqa: E731
    r1 = (await client.post("/api/v1/documents", json=body("acquirer file spec", "The acquirer file lists interchange, scheme fees and chargeback codes per merchant identifier." * 4), headers=H)).json()
    assert r1["status"] == "processed" and r1["salience"]["score"] >= 0.6, r1
    # old and made of words the tenant already holds: below the floor
    r2 = (await client.post("/api/v1/documents", json=body("acquirer file spec copy", "The acquirer file lists interchange, scheme fees and chargeback codes per merchant identifier." * 4, v=stale), headers=H)).json()
    assert r2["status"] == "declined" and r2["reason"].startswith("salience:"), r2
    r3 = (await client.post("/api/v1/documents", json=body("payout schedule", "Payouts settle T+2 for card volume and T+1 for wallet volume; weekends shift to Monday." * 4), headers=H)).json()
    assert r3["status"] == "processed", r3
    r4 = (await client.post("/api/v1/documents", json=body("refund policy", "Refunds are issued to the original instrument within five business days of approval." * 4), headers=H)).json()
    assert r4["status"] == "declined" and r4["reason"].startswith("budget:bulk:2/2"), r4
    # a different source has its own budget
    r5 = (await client.post("/api/v1/documents", json=body("dispute playbook", "Disputes are answered with the delivery proof and the signed order confirmation." * 4, s="ops"), headers=H)).json()
    assert r5["status"] == "processed", r5


@pytest.mark.asyncio
async def test_sql_source_is_read_through_and_memory_learns_the_map(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "sources_root", str(tmp_path))
    db = tmp_path / "oldledger.db"
    con = sqlite3.connect(db)
    con.executescript("""
        create table merchants(id integer primary key, name text, mcc text, country text);
        create table payments(id integer primary key, merchant_id integer, amount_cents integer, status text, settled_on text);
        create table staff_salaries(id integer primary key, name text, salary integer);
        insert into merchants values (1,'Blue Fern Bakery','5462','NL'),(2,'Kite Surf Co','5941','PT');
        insert into payments values (1,1,1250,'settled','2026-09-15'),(2,1,4000,'failed','2026-09-16'),(3,2,9900,'settled','2026-09-16');
        insert into staff_salaries values (1,'x',1);
    """)
    con.commit(); con.close()
    r = await client.post("/api/v1/sources", json={"name": "oldledger", "kind": "sql", "url": f"sqlite:///{db}", "tables": ["merchants", "payments"], "row_limit": 2}, headers=H)
    assert r.status_code == 201, r.text
    assert set(r.json()["schema_tables"]) == {"merchants", "payments"}
    # the map: the schema is in memory as keyed state before any query
    r = await client.get("/api/v1/memory/search", params={"q": "oldledger payments table columns", "type": "state"}, headers=H)
    keys = [n["key"] for n in r.json()["nodes"] if n["type"] == "Metric"]
    assert "state:source/oldledger/schema:payments" in keys, keys
    # read-through: rows come back, capped, with a query id; nothing is stored but the ledger row
    r = await client.post("/api/v1/sources/oldledger/query", json={"sql": "select m.name, p.amount_cents, p.status from payments p join merchants m on m.id = p.merchant_id order by p.id"}, headers=H)
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["row_count"] == 2 and q["truncated"] is True and q["columns"] == ["name", "amount_cents", "status"] and q["query_id"].startswith("query:")
    led = (await session.execute(select(Episode).where(Episode.org_id == "orgS", Episode.action_type == "source.queried"))).scalars().all()
    assert len(led) == 1 and led[0].payload["tables"] == ["merchants", "payments"] and "rows" in led[0].payload and "name" not in str(led[0].payload.get("content", ""))
    # guard rails: writes and non-allow-listed tables are refused
    assert (await client.post("/api/v1/sources/oldledger/query", json={"sql": "delete from payments"}, headers=H)).status_code == 422
    assert (await client.post("/api/v1/sources/oldledger/query", json={"sql": "select * from staff_salaries"}, headers=H)).status_code == 422
    # the agent writes what it learned, citing the query
    env = {"scope": "org", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "dana"},
           "content": "In oldledger, payments.status 'failed' rows are retried batches; settled_on is the acquirer date, not the request date.",
           "payload": {"kind": "learning", "topic": "source/oldledger/map", "key": "dedup", "evidence_ids": [q["query_id"]],
                       "value": "In oldledger, payments.status 'failed' rows are retried batches; settled_on is the acquirer date, not the request date."}}
    assert (await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=H)).status_code == 202
    md = (await client.get("/api/v1/memory/answer", params={"q": "what does settled_on mean in the oldledger payments table"}, headers=H)).json()["markdown"]
    assert "acquirer date" in md, md
