#!/usr/bin/env python3
"""magnet — the Q100 program: research what each person needs; keep the plays queue warm.

One tool, two duties: (1) fetch research material about a Q100 member from
every configured web provider, (2) persist everything durable — living
thesis, asks ledger, plays queue — in the contact_intelligence DB so runs
build on each other. Warm-path matching lives in the firm-contacts skill.

Usage:
    python magnet.py '{"op":"<op>", ...}'

Read ops:
    q100            Q100 roster with thesis, stalest thesis first (the worklist)
                    {op:"q100", status?:"confirmed", owner?}
    research_person the full multi-provider dossier fan-out for ONE person —
                    Exa social/news/web + Grok live X + Parallel (+ X API if
                    configured), all concurrently, each best-effort
                    {op:"research_person", name, org?, handles?:{x?}, days?:90}
    research        one follow-up Exa query when a thread needs pulling
                    {op:"research", query, scope?:"social"|"news"|"web", days?:90, limit?:10}
    queue           plays awaiting a human (the digest source)
                    {op:"queue", status?:"proposed", limit?:30}

Write ops:
    set_thesis    persist a member's living thesis after research
                  {op:"set_thesis", contact_id, thesis:{current_focus, hunting_for:[...],
                   whereabouts?:[...], recent_moves?:[...], sources_checked?:[...]}}
    record_asks   persist stated needs (from their own posts, or logged by a partner)
                  {op:"record_asks", asks:[{contact_id, ask, kind, source?, source_id?,
                   evidence?, asked_at?}]}
    propose_play  queue a play for human approval
                  {op:"propose_play", contact_id, action, ask_id?, match_name?, rationale?}
    update_play   move a play through its lifecycle
                  {op:"update_play", id, status, outcome?}

Returns JSON: {ok:true, op, count, results, ...} or
              {ok:false, error_kind:"bad_request"|"system", error:"..."}.

This tool fetches and records. Synthesizing research into a thesis, judging
what counts as a need, and finding matches (firm-contacts find/reach) are
the agent's job — see SKILL.md.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, timedelta
from typing import Any

import core
import providers

ASK_KINDS = ("intro", "info", "opportunity")
ASK_SOURCES = ("granola", "slack", "email", "manual")
PLAY_STATUSES = ("proposed", "approved", "sent", "done", "dismissed")
EXA_ENDPOINT = "https://api.exa.ai/search"
SOCIAL_DOMAINS = ["x.com", "twitter.com", "linkedin.com", "github.com"]


def ok(op: str, results: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"ok": True, "op": op, "count": len(results), "results": results, **extra}


def bad(msg: str) -> dict[str, Any]:
    return {"ok": False, "error_kind": "bad_request", "error": msg}


def system_err(msg: str) -> dict[str, Any]:
    return {"ok": False, "error_kind": "system", "error": msg}


# ── q100: the roster, stalest thesis first (the research worklist) ─────────
def op_q100(req: dict[str, Any]) -> dict[str, Any]:
    status = (req.get("status") or "confirmed").strip()
    sql = """
    SELECT m.contact_id, c.canonical_name AS name, c.func, c.role, o.name AS org,
           o.domain AS org_domain, m.owner_email AS owner, m.tier, m.status,
           m.thesis, m.thesis_at::date AS thesis_at,
           (SELECT max(co.last_seen) FROM contact_observers co
             WHERE co.contact_id = m.contact_id)::date AS last_touch,
           (SELECT array_agg(lower(ci.value)) FROM contact_identifiers ci
             WHERE ci.contact_id = m.contact_id AND ci.kind = 'email') AS emails,
           (SELECT ci.value FROM contact_identifiers ci
             WHERE ci.contact_id = m.contact_id AND ci.kind = 'linkedin_url' LIMIT 1) AS linkedin_url
    FROM q100_members m
    JOIN contacts c ON c.id = m.contact_id
    LEFT JOIN orgs o ON o.id = c.org_id
    WHERE m.status = %(status)s
      AND (%(owner)s IS NULL OR lower(m.owner_email) = lower(%(owner)s))
    ORDER BY m.thesis_at ASC NULLS FIRST, m.tier, c.canonical_name
    """
    rows = core.fetch(core.contact_dsn(), sql,
                      {"status": status, "owner": req.get("owner")}, service_role=True)
    return ok("q100", rows, status=status)


# ── research_person: the full multi-provider dossier for one member ────────
def op_research_person(req: dict[str, Any]) -> dict[str, Any]:
    name = (req.get("name") or "").strip()
    if len(name) < 2:
        return bad("`name` is required, e.g. {op:'research_person', name:'Jane Doe', org:'Acme'}")
    days = min(max(int(req.get("days", 90)), 1), 365)
    dossier = providers.research_person(name, (req.get("org") or "").strip() or None,
                                        req.get("handles"), days)
    if not dossier["sources_checked"]:
        return system_err(f"every provider failed or is unconfigured: {dossier['providers']}")
    return {"ok": True, "op": "research_person", "name": name, "days": days, **dossier}


# ── research: one follow-up Exa query when a thread needs pulling ───────────
def op_research(req: dict[str, Any]) -> dict[str, Any]:
    query = (req.get("query") or "").strip()
    if len(query) < 3:
        return bad("`query` is required, e.g. 'Jane Doe Acme Ventures investing focus'")
    scope = req.get("scope") or "social"
    if scope not in ("social", "news", "web"):
        return bad("`scope` must be social | news | web")
    days = min(max(int(req.get("days", 90)), 1), 365)
    limit = min(max(int(req.get("limit", 10)), 1), 25)

    api_key = core.env_value("EXA_API_KEY")
    if not api_key:
        try:
            api_key = core.secret_value(core.env_value("EXA_API_KEY_SECRET") or "exa-api-key")
        except Exception as exc:  # noqa: BLE001
            return system_err(f"EXA_API_KEY unavailable ({type(exc).__name__})")

    body: dict[str, Any] = {
        "query": query, "numResults": limit, "type": "auto",
        "contents": {"text": {"maxCharacters": 800}},
        "startPublishedDate": (date.today() - timedelta(days=days)).isoformat(),
    }
    if scope == "social":
        body["includeDomains"] = SOCIAL_DOMAINS
    elif scope == "news":
        body["category"] = "news"

    httpreq = urllib.request.Request(
        EXA_ENDPOINT, data=json.dumps(body).encode(), method="POST",
        headers={"x-api-key": api_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(httpreq, timeout=20) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return system_err(f"exa request failed: {type(exc).__name__}")

    results = []
    for item in payload.get("results", []):
        if not item.get("url"):
            continue
        results.append({
            "title": item.get("title") or "",
            "url": item["url"],
            "published": item.get("publishedDate"),
            "author": item.get("author"),
            "text": " ".join((item.get("text") or "").split())[:800],
        })
    return ok("research", results, query=query, scope=scope, days=days)


# ── set_thesis: persist the living thesis ──────────────────────────────────
def op_set_thesis(req: dict[str, Any]) -> dict[str, Any]:
    thesis = req.get("thesis")
    if not req.get("contact_id") or not isinstance(thesis, dict) or not thesis:
        return bad("`contact_id` and a non-empty `thesis` object are required, e.g. "
                   "{current_focus, hunting_for:[{need, kind, provenance, evidence:[{claim,url,date}], confidence}]}")
    rows = core.execute(core.contact_dsn(), """
        UPDATE q100_members SET thesis=%(thesis)s::jsonb, thesis_at=now(), updated_at=now()
        WHERE contact_id=%(contact_id)s AND status='confirmed'
        RETURNING contact_id, thesis_at
    """, {"contact_id": req["contact_id"], "thesis": json.dumps(thesis)})
    if not rows:
        return bad(f"no CONFIRMED q100_member with contact_id '{req['contact_id']}'")
    return ok("set_thesis", rows)


# ── record_asks: persist what they voiced or stated ────────────────────────
def op_record_asks(req: dict[str, Any]) -> dict[str, Any]:
    asks = req.get("asks")
    if not isinstance(asks, list) or not asks:
        return bad("`asks` must be a non-empty array of "
                   "{contact_id, ask, kind, source?, source_id?, evidence?, asked_at?}")
    # Validate EVERYTHING before any write, so bad_request never means
    # "partially applied".
    cleaned: list[dict[str, Any]] = []
    for i, a in enumerate(asks):
        if not a.get("contact_id") or not (a.get("ask") or "").strip():
            return bad(f"asks[{i}]: `contact_id` and `ask` are required")
        if a.get("kind") not in ASK_KINDS:
            return bad(f"asks[{i}]: `kind` must be one of {', '.join(ASK_KINDS)}")
        source = a.get("source") or "manual"
        if source not in ASK_SOURCES:
            return bad(f"asks[{i}]: `source` must be one of {', '.join(ASK_SOURCES)}")
        cleaned.append({
            "contact_id": a["contact_id"], "ask": a["ask"].strip(), "kind": a["kind"],
            "source": source, "source_id": a.get("source_id"),
            "evidence": a.get("evidence"), "asked_at": a.get("asked_at"),
        })
    ids = sorted({c["contact_id"] for c in cleaned})
    confirmed = {r["contact_id"] for r in core.fetch(core.contact_dsn(), """
        SELECT contact_id::text AS contact_id FROM q100_members
        WHERE contact_id::text = ANY(%(ids)s) AND status='confirmed'
    """, {"ids": ids})}
    unknown = [i for i in ids if i not in confirmed]
    if unknown:
        return bad(f"not confirmed q100_members: {', '.join(unknown)}")
    # One transaction: every ask lands or none do.
    inserted = core.execute_many(core.contact_dsn(), """
        INSERT INTO asks (contact_id, ask, kind, source, source_id, evidence, asked_at)
        VALUES (%(contact_id)s, %(ask)s, %(kind)s, %(source)s, %(source_id)s, %(evidence)s, %(asked_at)s)
        ON CONFLICT (contact_id, source, coalesce(source_id, ''), md5(ask)) DO NOTHING
        RETURNING id, contact_id, ask, kind
    """, cleaned)
    return ok("record_asks", inserted, submitted=len(asks),
              deduped=len(asks) - len(inserted))


# ── propose_play: queue an action for human approval ──────────────────────
def op_propose_play(req: dict[str, Any]) -> dict[str, Any]:
    if not req.get("contact_id") or not (req.get("action") or "").strip():
        return bad("`contact_id` and `action` are required")
    # Single statement so the play INSERT and the ask status flip commit (or
    # fail) together — plays has no dedupe index, so a retry after a partial
    # failure must never be possible.
    rows = core.execute(core.contact_dsn(), """
        WITH new_play AS (
            INSERT INTO plays (ask_id, contact_id, match_name, action, rationale)
            SELECT %(ask_id)s, %(contact_id)s, %(match_name)s, %(action)s, %(rationale)s
            WHERE EXISTS (SELECT 1 FROM q100_members m
                           WHERE m.contact_id = %(contact_id)s AND m.status='confirmed')
            RETURNING id, contact_id, match_name, action, status
        ), flip_ask AS (
            UPDATE asks SET status='matched'
            WHERE %(ask_id)s::uuid IS NOT NULL AND id = %(ask_id)s::uuid
              AND status='open' AND EXISTS (SELECT 1 FROM new_play)
        )
        SELECT * FROM new_play
    """, {
        "ask_id": req.get("ask_id"), "contact_id": req["contact_id"],
        "match_name": req.get("match_name"), "action": req["action"].strip(),
        "rationale": req.get("rationale"),
    })
    if not rows:
        return bad(f"contact_id '{req['contact_id']}' is not a confirmed q100_member")
    return ok("propose_play", rows)


# ── update_play: lifecycle ─────────────────────────────────────────────────
def op_update_play(req: dict[str, Any]) -> dict[str, Any]:
    if not req.get("id"):
        return bad("`id` is required")
    if req.get("status") not in PLAY_STATUSES:
        return bad(f"`status` must be one of {', '.join(PLAY_STATUSES)}")
    rows = core.execute(core.contact_dsn(), """
        UPDATE plays SET status=%(status)s, outcome=coalesce(%(outcome)s, outcome), updated_at=now()
        WHERE id=%(id)s
        RETURNING id, action, status, outcome
    """, {"id": req["id"], "status": req["status"], "outcome": req.get("outcome")})
    if not rows:
        return bad(f"no play with id '{req['id']}'")
    return ok("update_play", rows)


# ── queue: what's waiting for a human ──────────────────────────────────────
def op_queue(req: dict[str, Any]) -> dict[str, Any]:
    status = (req.get("status") or "proposed").strip()
    if status not in PLAY_STATUSES:
        return bad(f"`status` must be one of {', '.join(PLAY_STATUSES)}")
    limit = min(max(int(req.get("limit", 30)), 1), 100)
    sql = """
    SELECT p.id, p.action, p.match_name, p.rationale, p.status,
           p.created_at::date AS proposed_on,
           c.canonical_name AS for_person, o.name AS for_org, m.owner_email AS owner,
           a.ask, a.kind AS ask_kind, a.evidence, a.asked_at
    FROM plays p
    JOIN contacts c ON c.id = p.contact_id
    LEFT JOIN orgs o ON o.id = c.org_id
    LEFT JOIN q100_members m ON m.contact_id = p.contact_id
    LEFT JOIN asks a ON a.id = p.ask_id
    WHERE p.status = %(status)s
    ORDER BY p.created_at DESC
    LIMIT %(limit)s
    """
    rows = core.fetch(core.contact_dsn(), sql, {"status": status, "limit": limit},
                      service_role=True)
    return ok("queue", rows, status=status)


OPS = {"q100": op_q100, "research_person": op_research_person, "research": op_research,
       "queue": op_queue, "set_thesis": op_set_thesis, "record_asks": op_record_asks,
       "propose_play": op_propose_play, "update_play": op_update_play}


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps(bad("pass a JSON request, e.g. '{\"op\":\"q100\"}'")))
        return
    try:
        req = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        print(json.dumps(bad(f"invalid JSON: {exc}")))
        return
    op = req.get("op")
    if op not in OPS:
        print(json.dumps(bad(f"unknown op '{op}'. valid: {', '.join(OPS)}")))
        return
    try:
        print(json.dumps(OPS[op](req)))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps(system_err(f"{type(exc).__name__}: {exc}")))


if __name__ == "__main__":
    main()
