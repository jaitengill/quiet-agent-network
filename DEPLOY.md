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
-- review list (the SET is required: contact_observers is RLS-gated, and a
-- fresh session without it silently returns NULL for every strength)
set app.role = 'service';
select m.contact_id, c.canonical_name, o.name as org, m.owner_email,
       (select max(strength) from contact_observers co where co.contact_id = m.contact_id) as strength
from q100_members m join contacts c on c.id = m.contact_id
left join orgs o on o.id = c.org_id
where m.status = 'candidate' order by strength desc nulls last;

-- confirm (repeat per person / batch by ids); tier 1 = the top ~10
update q100_members set status = 'confirmed', tier = 2, owner_email = '<partner>@quiet.com'
where contact_id in ('<uuid>', ...);
```

Nothing runs against unconfirmed members: the roster op returns only
`status='confirmed'`, and the write ops (`set_thesis`, `record_asks`,
`propose_play`) refuse contacts that aren't confirmed members.

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

## 5. Provision the VM (quiet-agent-infra + boiler)

Add a module block to `quiet-agent-infra/terraform/agents.tf`, mirroring
`agent_analyst` (shape per agent-repo/docs/DEPLOY_ANALYST.md):

```hcl
agent_id                 = "network"
agent_name               = "Ninja Network"
agent_role               = "investing"
openclaw_exec_host       = "sandbox"
openclaw_sandbox_network = "bridge"   # Postgres + research APIs over the network
agent_repo               = "git@github.com:jaitengill/quiet-agent-network.git"  # transfer to quietcap org later; GitHub redirects transfers
agent_deploy_key_secret  = "quiet-agent-network-deploy-key"
data_dsn_secret          = "network-agent-contact-intel-dsn"

secrets = [
  "network-slack-bot-token",
  "network-slack-app-token",
  "network-agent-contact-intel-dsn",
  "openai-api-key",
  "exa-api-key",
  "xai-api-key",
  "parallel-api-key",
  "x-bearer-token",
]
```

Also needed: a new Slack app (bot + app-level token, socket mode — clone the
analyst app's manifest) stored as the two slack secrets above; `psycopg2` on
the VM (agent-analyst's setup already installs it — reuse that step); and if
sandbox egress is allowlisted, allow `api.exa.ai`, `api.x.ai`,
`api.parallel.ai`, `api.x.com`, and the Cloud SQL IP `10.98.0.3`. Sandbox
env at boot resolves:

- `CONTACT_INTEL_DSN` ← `network-agent-contact-intel-dsn`
- `OPENAI_API_KEY`, `EXA_API_KEY`, `XAI_API_KEY`, `PARALLEL_API_KEY`,
  `X_BEARER_TOKEN` (optional)

Grant partners via `team_members.allowed_agents` (the SRE agent's
`manage-team` skill: add + sync). Drop the bot into the shared channel.

**Posture note:** research APIs and the DB DSN share this sandbox — a
deliberate deviation from the "web research in a separate low-trust worker"
guidance in DEPLOY_ANALYST.md, accepted for the sandbox phase because the
research ops call fixed API endpoints only and the DSN's write scope is
asks/plays/thesis. Split a web worker before production posture.

## 6. Smoke test (on the VM, or locally with `.env`)

```bash
python skills/magnet/scripts/magnet.py '{"op":"q100"}'                              # roster
python skills/magnet/scripts/magnet.py '{"op":"research_person","name":"<someone>"}' # provider fan-out + status map
python skills/firm-contacts/scripts/contacts.py '{"op":"reach","target":"Anthropic"}'
python skills/magnet/scripts/magnet.py '{"op":"queue"}'                             # empty until first tick
```

Then in Slack: "run magnet on <person>" and check a thesis + plays land in
the DB before anything is posted.
