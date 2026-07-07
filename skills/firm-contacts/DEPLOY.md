# Deploying the firm-contacts skill to an agent VM

The skill is plain files + one Python tool that reads the `contact_intelligence`
Postgres DB directly (read-only). To make it runnable on an OpenClaw agent VM
you need: (1) the files on the VM's skills path, (2) a read-only DSN, (3) the
VM's SA able to read that DSN secret, (4) `psycopg2` + `OPENAI_API_KEY` present.

Target VM in these examples: **agent-analyst** (it already has psycopg2 +
openai-api-key from quiet-data-lookup). Swap the SA/paths for another agent.

---

## 1. Put the files where the VM loads skills

The VM mounts its agent repo's `skills/` at `/workspace/skills/`. Place
`firm-contacts/` in that repo (same place as `quiet-data-lookup`) and push:

```bash
# if analyst is the host:
cp -r quiet-ai-skills/skills/firm-contacts \
      quiet-agent-analyst/skills/firm-contacts
cd quiet-agent-analyst && git add skills/firm-contacts && \
  git commit -m "add firm-contacts skill" && git push
# the VM picks it up on its next repo sync
```

(If the VM also mounts `quiet-ai-skills`, you can leave it there instead.)

---

## 2. Create a read-only DB role

In Cloud SQL Studio (DB `contact_intelligence`) or via `gcloud sql connect`:

```sql
CREATE ROLE contact_reader LOGIN PASSWORD '<generate-a-strong-one>';
GRANT CONNECT ON DATABASE contact_intelligence TO contact_reader;
GRANT USAGE  ON SCHEMA public TO contact_reader;
GRANT SELECT  ON ALL TABLES IN SCHEMA public TO contact_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO contact_reader;
```

No `BYPASSRLS` needed: the skill runs `SET app.role='service'`, which satisfies
the `service` RLS policy on `contact_observers` (read-only, GUC-based — fine for
a trusted internal agent). The role only ever has SELECT.

---

## 3. Store the DSN as a secret

The platform instance's private IP is `10.98.0.3` (connection name
`atlas-486120:us-central1:quiet-platform-postgres`). Agent VMs reach it over the VPC.

```bash
echo -n "postgres://contact_reader:<pw>@10.98.0.3:5432/contact_intelligence?sslmode=require" \
  | gcloud secrets create contact-intelligence-readonly-dsn --data-file=-
```

(If your agents connect via the Cloud SQL Auth Proxy instead of private IP,
point the DSN at the proxy socket/port the way quiet-data-lookup's data DSN does.)

---

## 4. Grant the agent VM's SA access to the secret

```bash
gcloud secrets add-iam-policy-binding contact-intelligence-readonly-dsn \
  --member="serviceAccount:agent-analyst@atlas-486120.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

`core.py` fetches it at runtime via the VM's metadata token (no value on disk).
Alternatively, inject `CONTACT_INTEL_DSN=<dsn>` into the VM's `sandbox.env` and
skip the secret — `env_value()` checks env first.

---

## 5. Confirm the VM has the deps

- **psycopg2** — present on agent-analyst (quiet-data-lookup uses it). For a VM
  that lacks it: `pip install psycopg2-binary`.
- **OPENAI_API_KEY** — needed only for the semantic `find` op (embeds the query).
  `reach`, `filter`, `insights` work without it. agent-analyst has the shared
  `openai-api-key` secret; `core.py` falls back to it automatically.

---

## 6. Smoke-test on the VM

```bash
cd /workspace/skills/firm-contacts
python scripts/contacts.py '{"op":"reach","target":"Anthropic"}'        # who we know there
python scripts/contacts.py '{"op":"filter","func":"investor","limit":5}' # structured (no embedding)
python scripts/contacts.py '{"op":"find","query":"AI infra founder","limit":5}'  # semantic
python scripts/contacts.py '{"op":"insights"}'                           # firm-wide
```

Expect JSON with `ok:true` and `knowers[]` on each contact. Triage:
- **all ops return 0** → DSN wrong, or the role can't `SET app.role` (it can — re-check the DSN/role).
- **`reach`/`filter` work but `find` errors** → `OPENAI_API_KEY` not reachable (semantic only).
- **`error_kind:"system"` with a connection error** → VM can't reach `10.98.0.3` (VPC / Cloud SQL auth).

---

## 7. Verify the agent uses it

The agent auto-loads `SKILL.md`. Ask it: *"who do we know at Anthropic?"* and
confirm it routes to `firm-contacts` → `reach` and answers with the warm path.

---

## What changes over time

Nothing to redeploy as data grows — the skill queries live. The graph refreshes
on its own (contact-intelligence ingest hourly, enrich daily). Only re-push the
skill files when the tool/SQL itself changes.
