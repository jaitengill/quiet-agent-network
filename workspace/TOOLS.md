# Network Tools

Shared tool rules live in `core/context/tools.md`. This file declares what
Ninja Network actually uses.

## Skills

- `magnet` — the Q100 tool: roster + living theses, multi-provider web
  research (`research_person` fans out to Exa social/news/web, Grok live X,
  Parallel, and the X API when configured), and the durable ledger
  (asks, plays, thesis writes). Procedures for scheduled runs: `HEARTBEAT.md`.
- `firm-contacts` — the firm graph: warm paths (`reach`), thesis-fit
  discovery (`find`), structured filters, firm-wide insights. This is how
  plays get their warm path.

## Runtime config

- `CONTACT_INTEL_DSN` — the network agent's OWN Postgres role on the
  `contact_intelligence` DB (secret `network-agent-contact-intel-dsn`):
  SELECT on the graph; INSERT/UPDATE only on `asks`, `plays`, and
  `q100_members` thesis columns. See `DEPLOY.md` for the grants.
- `EXA_API_KEY` (secret `exa-api-key`) — exists already.
- `XAI_API_KEY` (secret `xai-api-key`) — Grok live X search.
- `PARALLEL_API_KEY` (secret `parallel-api-key`) — Parallel research.
- `X_BEARER_TOKEN` (secret `x-bearer-token`) — optional; direct X timelines.
- `OPENAI_API_KEY` (secret `openai-api-key`) — firm-contacts embeddings.

A missing research key degrades that provider only — `research_person`
reports per-provider status; never treat partial coverage as an error.

## Boundaries

- No email, DM, or any outbound contact with anyone outside the firm's
  Slack. The plays queue is the only output.
- No writer access beyond `asks`/`plays`/`q100_members` thesis columns; no
  Salesforce OAuth, no Gmail, no Drive, no raw SQL beyond the fixed ops.
- No web access outside the magnet research ops — no generic browsing or
  fetching. If a source class is missing, say so; don't improvise around it.
- Do not read or surface another agent's data paths (ninja_email mirror,
  personal-agent DB). Granola/call content is out of scope for this agent.
