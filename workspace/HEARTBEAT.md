# Heartbeat

tasks:

- name: q100-research-tick
  interval: 3h
  prompt: "Run the Magnet research tick. (1) `q100` — take the FIRST 5 members (they are ordered stalest-thesis-first; this makes the loop self-checkpointing). (2) For each member, one `research_person` call (name, org, known handles, days:90), plus at most 2 follow-up `research` calls if something big surfaced. (3) Synthesize their thesis against the previous one from `q100`: {current_focus, hunting_for:[{need, kind: deal_flow|lp_intros|hiring|customers|info|other, provenance: stated|inferred, evidence:[{claim,url,date}], confidence: high|med|low}], whereabouts:[{place,when,evidence_url}], recent_moves:[], sources_checked: <the providers/coverage the tool reported>}. Every claim cited; >6 months old = background, not a current need. (4) `set_thesis` for every member researched, even if unchanged (note 'no material change'). (5) Act ONLY on the diff: stated needs (their own posts/words) → `record_asks` (source manual, evidence = quote + URL); stated or high-confidence needs with a plausible match → firm-contacts (`reach` for named targets, `find` min_strength 30) → `propose_play` with the warm path in rationale; whereabouts near a partner city → `propose_play` for a meetup. (6) If a play is time-sensitive (travel, active fundraise), post it to the channel now, tagged to the owner partner; everything else waits for the morning queue. If the roster is empty or every provider is down, say so once, briefly. Reply HEARTBEAT_OK if there was nothing actionable."

- name: q100-morning-queue
  interval: 24h
  prompt: "Deliver the Q100 morning queue. `{\"op\":\"queue\"}` — if empty, reply HEARTBEAT_OK and post nothing. Otherwise post ONE message: grouped by Q100 person, warmest plays first. Per play: **{Person} ({Org})** — what they need (source + date), → the play via {partner} with strength and a one-line rationale, and the play id with 'reply approve / skip / done + outcome'. Close with open asks that found no match — partners often know someone the graph doesn't. Keep it tight; this message is the product."

# Notes

- Never research more than 5 members per tick — the schedule provides
  throughput, not any single run.
- Confirm interval syntax against the boiler's heartbeat implementation when
  provisioning; adjust `q100-morning-queue` to a 7am-local cron expression if
  supported.
