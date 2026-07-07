# quiet-agent-network

Ninja Network — Quiet's network agent. Answers any network question (warm
paths, discovery, lists — via `firm-contacts`) and runs the **Q100/Magnet
program**: for the firm's 100 most important relationships, continuously
research online what each person is hunting for and where they'll be, keep a
living thesis per person, and turn the diffs into a human-approved queue of
plays (intros, info, meetups) delivered in Slack.

Standalone OpenClaw agent on its own GCE VM (consumes `quiet-agent-boiler`
like agent-sre / agent-qc-finance). All durable state lives in the
`contact_intelligence` Postgres DB (schema: contact-intelligence-api
migration `0010_q100.sql`) — the agent is the voice and hands, never the
source of truth.

## What it does

- **`magnet`** — the Q100 tool. `research_person` fans out to every
  configured provider concurrently (Exa social/news/web, x.ai Grok live X
  search, Parallel, X API) and returns a labeled evidence dossier; the agent
  synthesizes the living thesis, records stated needs in the `asks` ledger,
  and proposes `plays` for a partner to approve. Scheduled rhythms (research
  tick, morning queue) live in `workspace/HEARTBEAT.md`.
- **`firm-contacts`** — the firm graph: `reach` (warmest path), `find`
  (semantic discovery), `filter`, `insights`. Copied from `quiet-ai-skills`
  (canonical home); plays get their warm path here.

## What it never does

- Contact anyone outside the firm's Slack. Plays are proposed, partners
  execute. No emails, no DMs.
- Batch-research the whole roster in one run — ≤5 members per tick,
  stalest-thesis-first, so the loop is self-checkpointing.
- Hold write access beyond `asks`, `plays`, and `q100_members` thesis
  columns (see DEPLOY.md grants).

## Layout

```
workspace/   SOUL / IDENTITY / TOOLS / AGENTS / HEARTBEAT — the OpenClaw contract
skills/
  magnet/         SKILL.md + scripts/{magnet.py,providers.py,core.py}
  firm-contacts/  SKILL.md + scripts/{contacts.py,core.py} (from quiet-ai-skills)
DEPLOY.md    schema, DB role + grants, Q100 seed, secrets, VM provisioning
```

## Local dev

```bash
cp .env.example .env   # fill in DSN + keys
python skills/magnet/scripts/magnet.py '{"op":"q100"}'
python skills/magnet/scripts/magnet.py '{"op":"research_person","name":"Jane Doe","org":"Acme"}'
```

Requires Python 3.10+ and `psycopg2`.
