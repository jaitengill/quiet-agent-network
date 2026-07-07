# contact_intelligence — schema reference

Read-only Postgres (pgvector). The `contacts.py` ops cover the common asks; this
doc is for when you need a custom query or to understand a field.

## RLS — important

`contact_observers`, `raw_snapshots`, `audit_log`, `reads` are row-level-security
gated to the `service` role. The tool sets `SET app.role = 'service'` on every
connection (see `core.fetch`). Without it those tables return **zero rows** —
which looks like "we don't know anyone." `contacts`, `orgs`,
`contact_identifiers` are firm-readable without it.

## Tables

### contacts — one row per real person (the node)
| column | meaning |
|---|---|
| `id` uuid | node id |
| `canonical_name` | best display name (may be null → filtered out) |
| `org_id` → orgs | their company |
| `func` | founder \| investor \| operator \| lawyer \| banker \| recruiter \| advisor \| other |
| `role` | free-text title / LinkedIn headline |
| `seniority` | junior \| mid \| senior \| exec \| unknown |
| `sectors` text[] | e.g. {fintech, AI} |
| `total_observers` int | # distinct partners who know them (firm-wide reach) |
| `embedding` vector(1024) | semantic vector (text-embedding-3-small @ 1024) |
| `is_automated` / `is_personal` / `is_synthetic` | exclude when true (noise/personal/blank) |

Always filter real contacts with:
`is_automated=false AND is_personal=false AND is_synthetic=false AND canonical_name IS NOT NULL AND btrim(canonical_name)<>''`

### contact_identifiers — the aliases (how you look someone up)
`(kind, value)` where kind ∈ `email | linkedin_url | phone`; `contact_id` → contacts.
One person can have several. Email + LinkedIn collapse onto the same contact.

### contact_observers — the relationships (the edges)
One row per (contact, partner, source). **This is where "who knows whom" lives.**
| column | meaning |
|---|---|
| `partner_email` | which Quiet partner |
| `source` | email \| linkedin \| linkedin_message \| phone_imported |
| `sent_count` / `received_count` | email volume + direction |
| `thread_count` / `meeting_count` | threads, calendar meetings |
| `recent_subjects` jsonb | `[{at, direction, subject}]` — the actual exchanges |
| `linkedin_degree` | 1/2/3 (connection distance) |
| `linkedin_bridges` jsonb | who can bridge a LinkedIn intro |
| `last_seen` | most recent interaction |
| `strength` smallint | 0–100 relationship rank (formula below) |
| `comp_recency`/`comp_frequency`/`comp_meetings`/`comp_reciprocity` | the components |
| `rel_state` | active \| warming \| cooling \| dormant \| nascent |
| `rel_summary` | AI relationship brief (1 partner's view) |
| `rel_next_step` | suggested next action |

### orgs — companies
`id, domain, name, type (vc|startup|bank|law|sovereign-wealth|recruiter|university|corp|consultancy|nonprofit|other), sector, stage (seed|a|b|c+|growth|public), description`.
LinkedIn-sourced orgs may have a `linkedin:<slug>` placeholder domain until resolved.

## Strength formula

`strength = round(100 · (0.28·R + 0.24·F + 0.30·M + 0.18·B))`
- **R** recency — exp decay on `last_seen` (180-day half-life)
- **F** frequency — log of `thread_count`
- **M** meetings — log of `meeting_count`
- **B** reciprocity — `min(sent,received)/max(sent,received)`

LinkedIn-only observers have no email signal → strength ≈ 0; judge them by
`linkedin_degree`, not strength.

## Data quality notes

- **Email contacts** are richly enriched (strength, briefs, recent_subjects).
- **LinkedIn-only contacts** (no email identifier) are thin — name + degree, and
  (from CSV exports) company + headline. No interaction history.
- A contact with `strength=0` and no emails is an address-book entry, not a
  relationship — say so rather than implying contact.
- The graph refreshes automatically: ingest hourly, AI enrichment daily.

## Custom SQL (beyond the ops)

If you must hand-write SQL, connect via `core.fetch(sql, params)` (it sets the
RLS role + read-only). Use psycopg2 `%(name)s` params — never inline `%x%`.
Example — partners who know the most fintech founders:

```sql
SELECT co.partner_email, count(DISTINCT c.id) AS founders
FROM contacts c JOIN contact_observers co ON co.contact_id = c.id
WHERE c.func='founder' AND 'fintech'=ANY(c.sectors)
  AND c.is_automated=false AND c.is_personal=false AND c.is_synthetic=false
GROUP BY co.partner_email ORDER BY founders DESC;
```
