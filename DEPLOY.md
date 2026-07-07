# Deploying Ninja Network

Standalone OpenClaw agent (same boiler pattern as agent-sre / agent-qc-finance).
Durable state lives in the `contact_intelligence` Postgres DB — schema comes
from contact-intelligence-api migration `0010_q100.sql`.

## 1. Apply the schema (contact-intelligence-api)

`0010_q100.sql` creates `q100_members`, `asks`, `plays`. Per that repo's own
deploy notes, run migrations manually via cloud-sql-proxy:

```bash
cd contact-intelligence-api
# proxy to quiet-platform-postgres, then:
npm run migrate           # applies 0010; verify with: npm run migrate -- --status
```

## 2. Create the agent's DB role (Cloud SQL Studio, DB `contact_intelligence`)

Least privilege: read the graph, write ONLY the ledger + thesis columns.

```sql
create role network_agent login password '<generate-a-strong-one>';
grant connect on database contact_intelligence to network_agent;
grant usage on schema public to network_agent;
grant select on all tables in schema public to network_agent;
grant insert, update on asks to network_agent;
grant insert, update on plays to network_agent;
grant update (thesis, thesis_at, updated_at) on q100_members to network_agent;
```

No `BYPASSRLS`: the skill sets `SET app.role='service'`, which satisfies the
GUC-based RLS policies on `contact_observers` (read-only; fine for a trusted
internal agent). `asks`/`plays`/`q100_members` carry no RLS — firm-shared
(T2) content by design.

## 3. Seed Q100 candidates, then curate

Run as the app/owner user (needs observer rows for ranking):

```sql
set app.role = 'service';

insert into q100_members (contact_id, owner_email, status)
select c.id,
       (select co.partner_email from contact_observers co
         where co.contact_id = c.id
         order by co.strength desc nulls last limit 1),
       'candidate'
from contacts c
where c.is_automated = false and c.is_personal = false and c.is_synthetic = false
  and c.canonical_name is not null and btrim(c.canonical_name) <> ''
  and not exists (select 1 from contact_identifiers ci
                   where ci.contact_id = c.id
                     and (ci.value ilike '%@quiet.com' or ci.value ilike '%@gpxcap.com'))
order by (select max(strength) from contact_observers co where co.contact_id = c.id) desc nulls last,
         c.total_observers desc
limit 250
on conflict (contact_id) do nothing;
```

Review candidates with the partners, then confirm the actual 100:

```sql
-- review list
select m.contact_id, c.canonical_name, o.name as org, m.owner_email,
       (select max(strength) from contact_observers co where co.contact_id = m.contact_id) as strength
from q100_members m join contacts c on c.id = m.contact_id
left join orgs o on o.id = c.org_id
where m.status = 'candidate' order by strength desc nulls last;

-- confirm (repeat per person / batch by ids); tier 1 = the top ~10
update q100_members set status = 'confirmed', tier = 2, owner_email = '<partner>@quiet.com'
where contact_id in ('<uuid>', ...);
```

Nothing runs against unconfirmed members: every skill op filters
`status='confirmed'`.

## 4. Secrets (Secret Manager, project atlas-486120)

```bash
echo -n "postgres://network_agent:<pw>@10.98.0.3:5432/contact_intelligence?sslmode=require" \
  | gcloud secrets create network-agent-contact-intel-dsn --data-file=-
echo -n "<xai key>"      | gcloud secrets create xai-api-key --data-file=-
echo -n "<parallel key>" | gcloud secrets create parallel-api-key --data-file=-
# optional (direct X timelines):
echo -n "<x bearer>"     | gcloud secrets create x-bearer-token --data-file=-
```

Already exist: `exa-api-key`, `openai-api-key`. Grant the agent VM's service
account `secretAccessor` on all of the above. A missing research key only
degrades that provider — `research_person` reports per-provider status.

## 5. Provision the VM (quiet-agent-boiler)

Same flow as agent-sre / agent-qc-finance: new VM + SA from
quiet-agent-boiler / quiet-agent-infra with `AGENT_REPO` pointed at this repo
(pin a ref), a new Slack app (bot + app-level token, socket mode) stored as
`network-slack-bot-token` / `network-slack-app-token`, and the secrets above
resolved into the sandbox env at boot:

- `CONTACT_INTEL_DSN` ← `network-agent-contact-intel-dsn`
- `OPENAI_API_KEY`, `EXA_API_KEY`, `XAI_API_KEY`, `PARALLEL_API_KEY`,
  `X_BEARER_TOKEN` (optional)

Needs `psycopg2` on the VM (agent-analyst's setup already installs it —
reuse that step). Drop the bot into the shared channel with the partners.

## 6. Smoke test (on the VM, or locally with `.env`)

```bash
python skills/magnet/scripts/magnet.py '{"op":"q100"}'                              # roster
python skills/magnet/scripts/magnet.py '{"op":"research_person","name":"<someone>"}' # provider fan-out + status map
python skills/firm-contacts/scripts/contacts.py '{"op":"reach","target":"Anthropic"}'
python skills/magnet/scripts/magnet.py '{"op":"queue"}'                             # empty until first tick
```

Then in Slack: "run magnet on <person>" and check a thesis + plays land in
the DB before anything is posted.
