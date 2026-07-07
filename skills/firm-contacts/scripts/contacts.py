#!/usr/bin/env python3
"""firm-contacts — query Quiet's unified contact graph (read-only).

Usage:
    python contacts.py '{"op":"<op>", ...}'

Ops:
    find     semantic discovery — "who fits this description"
             {op:"find", query:"seed-stage AI infra founder",
              func?, sector?, min_strength?, limit?}
    reach    who we know at a named company / person + warmest path
             {op:"reach", target:"Anthropic", limit?}
    filter   structured discovery (no embedding)
             {op:"filter", func?, sector?, org_type?, min_strength?, limit?}
    insights firm-wide: bridges (3+ partners), coverage by function, cooling
             {op:"insights"}

Returns JSON: {ok:true, op, count, results|...} or
              {ok:false, error_kind:"bad_request"|"system", error:"..."}.
Each contact carries `knowers[]` — every partner who knows them, HOW
(source, strength, LinkedIn degree, sent/received/meetings, last_seen,
recent email subjects, AI brief) — i.e. the warm-intro path.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import core

# Shared predicates -----------------------------------------------------------
REAL = ("c.is_automated = false AND c.is_personal = false AND c.is_synthetic = false "
        "AND c.canonical_name IS NOT NULL AND btrim(c.canonical_name) <> ''")
# Drop LinkedIn-scraper slop (LinkedIn-only single-token / UI-fragment names).
NOT_SLOP = (
    "NOT (NOT EXISTS (SELECT 1 FROM contact_identifiers ci WHERE ci.contact_id = c.id AND ci.kind='email') "
    "AND (c.canonical_name !~ '\\s' OR c.canonical_name ~* "
    "'^(posts?|status|connect|message|follow(ing)?|pending|member|profile|see more|show more|view|open)$'))"
)
KNOWERS = """
  (SELECT jsonb_agg(jsonb_build_object(
     'partner', co.partner_email, 'source', co.source, 'strength', co.strength,
     'degree', co.linkedin_degree, 'state', co.rel_state, 'last_seen', co.last_seen,
     'sent', co.sent_count, 'received', co.received_count, 'meetings', co.meeting_count,
     'recent_subjects', co.recent_subjects, 'brief', co.rel_summary, 'bridges', co.linkedin_bridges
   ) ORDER BY co.strength DESC NULLS LAST, co.linkedin_degree ASC NULLS LAST)
   FROM contact_observers co WHERE co.contact_id = c.id) AS knowers
"""


def ok(op: str, results: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"ok": True, "op": op, "count": len(results), "results": results, **extra}


def bad(msg: str) -> dict[str, Any]:
    return {"ok": False, "error_kind": "bad_request", "error": msg}


# ── find: semantic discovery, reranked by reachability ───────────────────────
def op_find(req: dict[str, Any]) -> dict[str, Any]:
    query = (req.get("query") or "").strip()
    if len(query) < 2:
        return bad("`query` is required (≥2 chars), e.g. 'seed-stage AI infra founder'")
    limit = min(max(int(req.get("limit", 20)), 1), 60)
    vec, err = core.embed_query(query)
    if err:
        return {"ok": False, "error_kind": "system", "error": err}
    sql = f"""
    WITH cand AS (
      SELECT c.id, c.canonical_name AS name, c.func, c.seniority, c.sectors,
             o.name AS org, o.type AS org_type,
             (c.embedding <=> %(vec)s::vector) AS dist
      FROM contacts c LEFT JOIN orgs o ON o.id = c.org_id
      WHERE c.embedding IS NOT NULL AND {REAL} AND {NOT_SLOP}
        AND (%(func)s IS NULL OR c.func = %(func)s)
        AND (%(sector)s IS NULL OR %(sector)s = ANY(c.sectors))
      ORDER BY c.embedding <=> %(vec)s::vector
      LIMIT 120
    ),
    enriched AS (
      SELECT cand.*, c.id AS cid,
             COALESCE((SELECT max(strength) FROM contact_observers co WHERE co.contact_id=c.id),0) AS best_strength,
             (SELECT min(linkedin_degree) FROM contact_observers co WHERE co.contact_id=c.id AND co.source='linkedin') AS best_degree,
             {KNOWERS}
      FROM cand JOIN contacts c ON c.id = cand.id
    )
    SELECT name, func, seniority, sectors, org, org_type,
           round((1-dist)::numeric,3) AS similarity, best_strength, best_degree, knowers,
           round((
             (1-dist)*0.6 + LEAST(best_strength/100.0,1)*0.3
             + (CASE WHEN best_degree=1 THEN 0.10 WHEN best_degree=2 THEN 0.05 ELSE 0 END)
           )::numeric, 4) AS score
    FROM enriched
    WHERE (%(min_strength)s IS NULL OR best_strength >= %(min_strength)s)
    ORDER BY score DESC
    LIMIT %(limit)s
    """
    rows = core.fetch(sql, {
        "vec": core.vector_literal(vec), "func": req.get("func"), "sector": req.get("sector"),
        "min_strength": req.get("min_strength"), "limit": limit,
    })
    return ok("find", rows, query=query)


# ── reach: who we know at a named company / person ───────────────────────────
def op_reach(req: dict[str, Any]) -> dict[str, Any]:
    target = (req.get("target") or "").strip()
    if len(target) < 2:
        return bad("`target` is required (a company or person name), e.g. 'Anthropic'")
    limit = min(max(int(req.get("limit", 50)), 1), 150)
    sql = f"""
    SELECT c.canonical_name AS name, c.func, c.seniority, o.name AS org, o.domain, o.type AS org_type,
           COALESCE((SELECT max(strength) FROM contact_observers co WHERE co.contact_id=c.id),0) AS best_strength,
           (SELECT min(linkedin_degree) FROM contact_observers co WHERE co.contact_id=c.id AND co.source='linkedin') AS best_degree,
           {KNOWERS}
    FROM contacts c LEFT JOIN orgs o ON o.id = c.org_id
    WHERE {REAL} AND {NOT_SLOP}
      AND (c.canonical_name ILIKE %(like)s OR o.name ILIKE %(like)s OR o.domain ILIKE %(like)s
           OR EXISTS (SELECT 1 FROM contact_identifiers ci WHERE ci.contact_id=c.id AND ci.value ILIKE %(like)s))
    ORDER BY best_strength DESC NULLS LAST, best_degree ASC NULLS LAST
    LIMIT %(limit)s
    """
    rows = core.fetch(sql, {"like": core.like_wrap(target), "limit": limit})
    return ok("reach", rows, target=target)


# ── filter: structured discovery (no embedding) ──────────────────────────────
def op_filter(req: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(req.get("limit", 30)), 1), 100)
    sql = f"""
    SELECT c.canonical_name AS name, c.func, c.seniority, c.sectors,
           o.name AS org, o.type AS org_type, c.total_observers,
           COALESCE((SELECT max(strength) FROM contact_observers co WHERE co.contact_id=c.id),0) AS best_strength,
           {KNOWERS}
    FROM contacts c LEFT JOIN orgs o ON o.id = c.org_id
    WHERE {REAL} AND {NOT_SLOP}
      AND (%(func)s IS NULL OR c.func = %(func)s)
      AND (%(sector)s IS NULL OR %(sector)s = ANY(c.sectors))
      AND (%(org_type)s IS NULL OR o.type = %(org_type)s)
    ORDER BY (SELECT max(strength) FROM contact_observers co WHERE co.contact_id=c.id) DESC NULLS LAST,
             c.total_observers DESC
    LIMIT %(limit)s
    """
    rows = core.fetch(sql, {
        "func": req.get("func"), "sector": req.get("sector"),
        "org_type": req.get("org_type"), "limit": limit,
    })
    # min_strength post-filter (keeps the SQL simple).
    ms = req.get("min_strength")
    if ms is not None:
        rows = [r for r in rows if (r.get("best_strength") or 0) >= ms]
    return ok("filter", rows)


# ── insights: the cross-partner derived layer ────────────────────────────────
def op_insights(_req: dict[str, Any]) -> dict[str, Any]:
    bridges = core.fetch(f"""
      SELECT c.canonical_name AS name, c.func, o.name AS org, c.total_observers
      FROM contacts c LEFT JOIN orgs o ON o.id=c.org_id
      WHERE c.total_observers >= 3 AND {REAL}
      ORDER BY c.total_observers DESC, c.canonical_name LIMIT 50
    """)
    coverage = core.fetch(f"""
      SELECT COALESCE(c.func,'(untagged)') AS func, count(*)::int AS contacts
      FROM contacts c WHERE {REAL} GROUP BY c.func ORDER BY contacts DESC
    """)
    decay = core.fetch(f"""
      SELECT c.canonical_name AS name, co.partner_email, co.strength, co.last_seen::date AS last_seen, o.name AS org
      FROM contact_observers co JOIN contacts c ON c.id=co.contact_id LEFT JOIN orgs o ON o.id=c.org_id
      WHERE co.strength >= 40 AND co.last_seen < now() - interval '180 days' AND {REAL}
      ORDER BY co.strength DESC LIMIT 50
    """)
    return {"ok": True, "op": "insights", "bridges": bridges,
            "coverage_by_function": coverage, "decaying_relationships": decay}


OPS = {"find": op_find, "reach": op_reach, "filter": op_filter, "insights": op_insights}


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps(bad("pass a JSON request, e.g. '{\"op\":\"reach\",\"target\":\"Anthropic\"}'")))
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
        print(json.dumps({"ok": False, "error_kind": "system", "error": f"{type(exc).__name__}: {exc}"}))


if __name__ == "__main__":
    main()
