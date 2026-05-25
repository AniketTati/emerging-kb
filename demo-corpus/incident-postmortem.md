# Incident Postmortem — Vertex AI Platform outage, March 18, 2026

**Authors**: Ramesh Gupta (SRE on-call), Priya Menon (Platform engineering lead)
**Status**: Final
**Reviewed by**: Rajesh Sharma (CEO), Maya Iyer (CTO)
**Date**: March 24, 2026

## Summary

On March 18, 2026 between **04:12 and 06:42 UTC**, the Vertex AI Platform
returned 5xx errors for all document-ingestion requests in the
**ap-south-1** (Mumbai) region. The outage lasted **2 hours 30 minutes**
and affected approximately **3,400** customer-submitted documents,
including all NorthWind Capital traffic for that window. Service was
restored after a manual database failover.

Customer impact: **two enterprise customers** filed support tickets
referencing the outage. SLA credits owed: **NorthWind $1,000** (uptime
breach for the month), **Pinnacle Holdings $0** (within SLA threshold).

## Timeline (UTC)

| Time | Event |
|---|---|
| 04:12 | Primary RDS Postgres in ap-south-1 begins reporting elevated CPU |
| 04:18 | Connection pool exhaustion on attachment-ingestor pods |
| 04:24 | First 503s returned to customers; PagerDuty fires |
| 04:30 | On-call (Ramesh) acks alert; opens incident channel |
| 04:42 | Root cause hypothesis: runaway query from new analytics job |
| 04:58 | Analytics job killed; CPU drops to 60% |
| 05:14 | New connections still failing — pool not recovering |
| 05:32 | Failover to standby RDS initiated |
| 05:48 | Failover complete; ingestion restored at 5% throughput |
| 06:12 | Throughput at 80% as backed-up jobs drain |
| 06:42 | Throughput at 100%; incident declared resolved |
| 09:00 | Customer communication sent to NorthWind, Pinnacle Holdings |

## Root cause

A new analytics job introduced in commit `b14e9c2` (deployed
March 17, 2026 at 22:40 UTC) ran an unbounded `SELECT` against the
`document_chunks` table without a `LIMIT` clause. By 04:12 UTC the next
day, the query had accumulated enough rows to saturate Postgres CPU and
hold all connections from the application pool.

Compounding factors:

1. The pgbouncer connection pool's `server_idle_timeout` was set to
   1 hour, so connections held by the analytics process were not
   reaped quickly enough to relieve pressure.
2. The RDS standby was 38 seconds behind primary (well within tolerance
   but contributing to data lag after failover).
3. The on-call runbook for "connection pool exhaustion" was 9 months
   stale and pointed to a since-renamed dashboard.

## Action items

| # | Action | Owner | Due | Status |
|---|---|---|---|---|
| 1 | Add CI guard rejecting any SQL in `analytics/` without explicit `LIMIT` | Priya Menon | March 31 | Open |
| 2 | Lower `server_idle_timeout` to 5 minutes in pgbouncer config | Ramesh Gupta | March 25 | **Done** |
| 3 | Refresh "connection pool exhaustion" runbook with current dashboards | Ramesh Gupta | April 1 | Open |
| 4 | Migrate analytics jobs to read replica instead of primary | Maya Iyer | April 15 | Open |
| 5 | Send SLA credit notice to NorthWind for $1,000 | Sarah Chen (CFO Vertex) | March 26 | **Done** |
| 6 | Add synthetic monitoring for ingestion endpoint (every 60s, 3 regions) | Priya Menon | April 8 | Open |

## What went well

- On-call ack within 6 minutes of page.
- Failover playbook was up-to-date and executed cleanly.
- Customer communication, while delayed, was honest and specific.

## What didn't go well

- Runbook staleness slowed the diagnosis by an estimated 20 minutes.
- No synthetic monitoring meant we relied on customer-affecting errors
  to detect the outage.
- The analytics job was deployed Friday evening with no Monday-morning
  follow-up review.

## Glossary

- **SRE**: Site Reliability Engineering
- **RDS**: AWS Relational Database Service
- **SLA**: Service Level Agreement (Vertex commits to 99.95% monthly uptime)
