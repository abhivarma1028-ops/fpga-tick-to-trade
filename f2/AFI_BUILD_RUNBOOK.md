# F2 Status & Remaining Path — cl_tick_to_trade

**Date:** 2026-07-01  |  **Region:** us-east-1  |  **Account:** 546517269032

## TL;DR — the build is DONE. Only the on-hardware run remains, and it's quota-gated.

| Artifact | State (verified 2026-07-01) |
|---|---|
| AFI | `afi-0a02e7745a17daa6a` / `agfi-006b5fd42e5f1cb6e` — **available** (built 2026-06-18, WNS +0.066 ns) |
| DCP tarball | `s3://hft-fpga-afi-546517269032/dcp/hft_build.Developer_CL.tar` (17 MB) — present |
| z1d build host | `i-05d781d69c8424ccb` (z1d.2xlarge) — **stopped**. Not needed unless rebuilding. |
| Run scripts | `f2/launch_f2_run.sh` (from laptop) + `f2/run_on_f2.sh` (on instance) — ready |
| **BLOCKER** | F-instance quota `L-74FC7D96` still **8** (need ≥24). Was **policy-denied** 2026-06-23. |

## Remaining step (once F quota ≥24 is granted)
Turnkey — everything below is already scripted:
```bash
# from the laptop; launches f2.6xlarge, rsyncs f1/sim/host, loads AGFI, runs host_replay --device
./f2/launch_f2_run.sh
# prints stop/terminate commands at the end — TERMINATE when done (f2.6xlarge is ~$1.65/hr)
```
`run_on_f2.sh` on the instance does: `fpga-load-local-image -S 0 -I agfi-006b5fd42e5f1cb6e`
→ find BAR0 → `host_replay.py --device` → diff decisions/latency vs `phase2_golden`.

## Getting past the quota gate
The F quota was rejected as **new-account policy**, not a technical/justification problem, so a
plain CLI re-file may just auto-close again. See `f2/QUOTA_REREQUEST.md` for the CLI attempt and
`f2/aws_sales_request.md` for the sales/account-review escalation (lead with "AFI already built").

## Rebuild the AFI (ONLY if it ever gets deleted — not needed now)
The AFI is durable and free to keep, so skip this unless it disappears. If needed:
1. `aws ec2 start-instances --instance-ids i-05d781d69c8424ccb` (stop when done — ~$0.74/hr).
   ⚠️ Host has **no IAM instance profile** — `aws configure` on it or attach a role for S3 access.
2. On host: `cd ~/aws-fpga && source hdk_setup.sh`; rsync the CL design; run
   `aws_build_dcp_from_cl.py -c cl_tick_to_trade -f BuildAll --no-encrypt -t hft_build`
   (clock recipes A1 B2 C0 H2; ~23 min last time).
3. Upload `build/checkpoints/to_aws/*.Developer_CL.tar` to S3, then `aws ec2 create-fpga-image`.
