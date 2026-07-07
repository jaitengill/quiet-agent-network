---
name: firm-contacts
description: Query Quiet's firm-wide contact graph — the unified network of every partner's contacts (email + LinkedIn) enriched with company, role, relationship strength, recency, and AI relationship briefs. Use whenever someone asks who the firm knows, the warmest path / best route / intro to a person or company, who to invite to an event, who to add to a syndication or co-investor list, who we know in a sector or stage, or to find people who fit a profile or thesis. Trigger eagerly on "who do we know at…", "who's our way into…", "intro to…", "find me a…", "who covers…", "build a list of…". Do NOT use for deal/CRM pipeline state, fund performance, or documents — those live in quiet-data-lookup / quiet-fund-performance.
---

# Firm Contact Intelligence

Read-only query over the unified firm contact graph: ~19k real people across
the partners' combined Gmail + LinkedIn, deduped to one node per person, with
company, function, relationship strength, recency, recent email subjects, and
AI relationship briefs. One Python tool, four ops, one DSN.

The job: pick the op, get the data, answer with the **warm path** — never just
a name, always *who* at the firm knows them and *how*.

## How to call the tool

```bash
python /home/quiet/.openclaw/workspace-network/skills/firm-contacts/scripts/contacts.py '{"op":"<op>", ...}'
```

Returns JSON. If `ok:false` and `error_kind:"bad_request"`, fix the request shape
(the `error` says what) and retry once. If `error_kind:"system"`, the tool failed
(DB or embedding) — tell the user briefly and stop.

Every contact result carries `knowers[]` — each partner who knows them, with
`source` (email|linkedin), `strength` (0–100), `degree` (LinkedIn 1st/2nd/3rd),
`sent`/`received`/`meetings`, `last_seen`, `recent_subjects`, and `brief`. That
array IS the warm-intro path; lead with it.

---

## Routing table

| User is asking for… | op |
|---|---|
| "Who's our way into **<company>**?" / "Who do we know at X?" / "intro to **<person>**" | `reach` |
| "Find me a **<kind of person>**" / "who fits this thesis" / "people like X" | `find` |
| "Who do we know that's a **<role>** in **<sector>**?" (structured, no fuzzy meaning) | `filter` |
| Event guest list — "who should we invite to a **<topic>** dinner" | `find` (describe the audience) |
| Syndication / co-investor list — "investors for a **<stage> <sector>** round" | `find` with `func:"investor"` (+ `sector`) |
| "Where are we strong / blind?", "who does the whole firm know", "relationships going cold" | `insights` |
| Deal status, pipeline, memos, fund returns | NOT this skill — use quiet-data-lookup / quiet-fund-performance |

Default when it's a named entity → `reach`. When it's a description → `find`.

---

## Named patterns

### Warm intro / best route (the most common ask)

```
{"op":"reach","target":"Anthropic"}
```
Returns everyone we know there, ordered warmest-first. Read the top result's
`knowers[0]`: the partner with the highest email `strength` (or lowest LinkedIn
`degree`) is the best route. Quote how (`"Matt — 38 emails ↔, last Dec 2025"`),
the `brief` if present, and any `bridges` for a LinkedIn intro.

### Event / dinner guest list

```
{"op":"find","query":"applied-AI founders and operators in NYC","limit":30}
```
Group the results by who can reach each person. Present as: name · role · org ·
warmest partner. Note coverage ("30 strong fits; 22 reachable via the team").

### Syndication / co-investor list

```
{"op":"find","query":"Series A enterprise SaaS investors who co-invest","func":"investor","sector":"enterprise","min_strength":30,"limit":25}
```
`min_strength:30` keeps it to investors a partner actually has a relationship
with (not cold LinkedIn-only). Rank by `score` (fit × reachability).

### Sector / role coverage

```
{"op":"filter","func":"founder","sector":"fintech","limit":50}
```
Deterministic — every founder tagged fintech, strongest relationships first.
Use `filter` (not `find`) when the ask is a clean attribute match, not a vibe.

### Firm-wide read

```
{"op":"insights"}
```
`bridges` = contacts 3+ partners share (institutional relationships).
`coverage_by_function` = where the firm is deep vs thin. `decaying_relationships`
= once-strong ties gone quiet 180d+ (re-engagement candidates).

---

## Output modes

**Mode A — Direct route.** "Who's our way into X?" → lead with the single warmest
path: "**Matt** is your best route to Anthropic — 38 emails (two-way), last Dec
2025; active relationship. Brief: …". Add 1–2 backups. Stop.

**Mode B — Ranked list.** Event/syndication/discovery. 5–15 rows: name · role ·
org · **warmest path**. State coverage and how many are reachable vs cold.

**Mode C — Firm read.** `insights`. Labeled sections (Shared relationships /
Coverage / Cooling), short bullets, Slack mrkdwn.

Always distinguish **email relationships** (real correspondence — cite volume,
direction, recency) from **LinkedIn connections** (reach only — cite degree).
"Matt is 1st-degree on LinkedIn" ≠ "Matt has a warm email relationship."

---

## Key rules

- **Lead with the path, not the name.** A fit nobody can reach is worth less than
  a good fit a partner is 1st-degree to. `find` already reranks for this; trust `score`.
- **Email strength vs LinkedIn degree are different signals.** Don't call a passive
  LinkedIn 2nd-degree connection "a relationship." Use `source` to tell them apart.
- **`reach` matches person OR company OR domain OR email** — so it works for "Anthropic",
  "anthropic.com", or a person's name. Prefer a company name for a roster.
- **Be honest about gaps.** LinkedIn-only contacts have thin data (no email history,
  often no brief). If a discovery query returns mostly thin matches, say so.
- **Never invent contacts, emails, or paths.** Only report what the tool returns.
- **Don't expose another partner's private brief as fact about the contact** — attribute
  it ("Per Matt's notes: …"). Briefs are one partner's view of the relationship.

---

## References

- `references/schema.md` — tables, columns, the strength formula, function/sector
  vocabularies, and the RLS note (why the tool sets `app.role`).
