# Vertex Platform Team — Weekly Standup, 2026-04-20

**Attendees**: Priya Menon (lead), Ramesh Gupta, Vikram Iyer, Anika Rao,
Devansh Kumar, Sneha Patel

**Notes by**: Anika Rao
**Next meeting**: 2026-04-27 09:30 IST

## Last week — completed

- **Priya**: Shipped the `analytics/` lint rule from postmortem action
  item #1. Caught 14 existing offenders during retro-scan; assigned to
  individual owners.
- **Ramesh**: Pgbouncer config rollout in all three regions (action
  item #2). Verified via load test; no regressions.
- **Vikram**: Closed the NorthWind feature request for per-document
  audit log export. Shipped behind feature flag, NorthWind validated.
- **Anika**: Migrated the integration test suite from CircleCI to
  Buildkite; build time down from 38 min to 14 min on hot cache.
- **Devansh**: Onboarded Pinnacle Holdings; their first ingestion
  batch (412 docs) completed on time.
- **Sneha**: Wrote the design doc for the chunk-text dedup; Priya to
  review by Friday.

## This week — committed

- **Priya**: Review Sneha's chunk-text dedup design doc; schedule the
  technical review with Maya Iyer for Thursday.
- **Ramesh**: Implement the synthetic monitor (action item #6 from
  postmortem). Target: deployed in staging by Thursday.
- **Vikram**: Start scoping the multi-region failover automation.
- **Anika**: Pair with Devansh on the Helios Analytics evaluation
  pipeline; they want a POC by April 30.
- **Devansh**: Continue Helios POC; also support Sneha if she needs
  Postgres expertise on the dedup project.
- **Sneha**: Revise the dedup design doc based on Priya's review;
  start the prototype branch.

## Blockers

- **Vikram**: Multi-region failover automation needs sign-off from
  Maya on the proposed CRDT-based state machine. Will request 30 min
  on Wed.
- **Anika**: Buildkite spot-instance availability has been flaky; have
  filed a support ticket with their team. Workaround: fallback to
  on-demand for critical-path jobs.

## Announcements

- **All-hands Friday at 10:00 IST**: Rajesh will share the Q1 numbers
  and the Series B closing milestones.
- **New hire**: Ankit Sharma joins the Platform team on May 5 — he'll
  pair with Devansh for his first month.
- **Open positions**: Still hiring for one Senior SRE in Pune and one
  Staff Engineer (any location). Referrals appreciated.
