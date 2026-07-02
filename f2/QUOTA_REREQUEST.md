# F-Instance Quota — Re-Request & Escalation (F2 run)

**Date:** 2026-07-01  |  **Region:** us-east-1  |  **Account:** 546517269032 (root)

## Current state (verified 2026-07-01)
| Quota | Code | Need | Current | Status |
|---|---|---|---|---|
| Standard On-Demand vCPU | L-1216C47A | 16 (z1d host) | **16** | GRANTED ✅ |
| Running On-Demand F instances | L-74FC7D96 | 24 (f2.6xlarge) | **8** | **policy-denied**, not granted ⚠️ |

Three requests filed (06-17 Standard granted; two F requests 06-18 & 06-22 both DesiredValue 24).
Both F requests show `CASE_CLOSED` but effective quota stayed **8**. Per 2026-06-23 notes, AWS's
internal team **rejected** the F increase as **new-account policy** ("reach out to your account
point of contact" = boilerplate; no POC on Basic support). This is a **policy** denial, not a
weak-justification one — so a plain re-file may auto-close again without a human review.

## Why 8 is insufficient
f2.6xlarge (smallest F2, 1 FPGA) = **24 vCPU**. 8 vCPU only covers the EOL f1.2xlarge.
A real launch returns `VcpuLimitExceeded` (dry-run gives a false positive — ignore it).
Request exactly **24** (one f2.6xlarge). Don't over-ask.

## Path A — CLI re-file (low effort, try first)
```bash
aws service-quotas request-service-quota-increase \
  --service-code ec2 --quota-code L-74FC7D96 --desired-value 24 --region us-east-1
# then watch it:
aws service-quotas list-requested-service-quota-change-history \
  --service-code ec2 --region us-east-1 \
  --query 'RequestedQuotas[?QuotaCode==`L-74FC7D96`].{Id:Id,Val:DesiredValue,Status:Status,Updated:LastUpdated}'
```
File it from an **IAM user**, not root, if possible — root requests get deprioritized.

## Path B — escalate (what actually moves a policy denial) → see `f2/aws_sales_request.md`
The strongest card: **a validated AFI already exists** (`afi-0a02e7745a17daa6a`). That proves
real intent (miners don't run full HDK synthesis + P&R). Routes, in order:
1. AWS Sales contact form: https://aws.amazon.com/contact-us/sales-support/
2. Account & Billing support case (free on every tier) → category "Account".

Ready-to-paste messages (long + short) are in `f2/aws_sales_request.md`.

## Does NOT block anything else
The AFI is already built and available; the DCP is in S3. Only the on-hardware f2.6xlarge run is
gated. See `f2/AFI_BUILD_RUNBOOK.md`.
