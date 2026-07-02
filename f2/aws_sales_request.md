# AWS Sales / Account-Review Request — Enable EC2 F-instance (FPGA) access

**Account:** 546517269032   **Region:** US East (N. Virginia / us-east-1)
**Status:** Service Quotas request for "Running On-Demand F instances" (24 vCPUs)
was rejected by the internal team on 2026-06-23 (new-account policy, not technical).

## Where to send (in order of preference)
1. **AWS Sales contact form:** https://aws.amazon.com/contact-us/sales-support/
2. **Account & Billing support case** (free on every support tier):
   Support Center → Create case → "Account" → describe + paste the message below.
3. If AWS replies asking for more detail, reply in-thread (keep the AFI evidence front and centre).

## Strongest card
A **built, validated AFI already exists** (`afi-0a02e7745a17daa6a`). That proves
real, non-abusive intent — crypto miners don't go through full HDK synthesis +
place & route. Lead with it.

## Why exactly 24 vCPUs (not more, not less) — say this explicitly
24 is not an arbitrary number: it is the size of **one `f2.6xlarge`**, the **smallest**
F2 instance AWS offers (24 vCPU / 1 FPGA). I cannot request less — the next size down
does not exist in the F2 family; f2.12xlarge (48) and f2.48xlarge (192) are larger than
I need. So 24 = the minimum viable request for a single-FPGA validation run. I am not
asking for scale, just the one smallest FPGA instance. Making this explicit removes the
"why do you need this much" objection that a bare Service Quotas request cannot answer
(the Service Quotas API has no justification field — which is why this must go through a
support case).

---

## Message (ready to paste)

**Subject:** Request to enable EC2 F-instance (FPGA) access — account 546517269032

Hello,

I'm writing to request an account review to enable **EC2 F-instance (FPGA) access**
in **US East (N. Virginia)** on account **546517269032**. My Service Quotas request
to raise "Running On-Demand F instances" to 24 vCPUs (for a single `f2.6xlarge`) was
rejected by the internal team, and I was advised to reach out to my account point of
contact.

**Who I am / use case:** I am an FPGA hardware engineer running a personal R&D
project — a low-latency "tick-to-trade" market-data accelerator written in
SystemVerilog (ITCH parser → limit order book → trading-signal logic → pre-trade
risk checks → order emission). This is for hardware benchmarking and learning, not a
production trading operation.

**Why this is a legitimate, low-risk request — I've already done the hard part on AWS:**
- I completed the full AWS FPGA build flow on a `z1d.2xlarge` running the FPGA
  Developer AMI: synthesized, placed, and routed the design against the Virtex
  UltraScale+ (`xcvu47p`) F2 target using the AWS HDK.
- The design **closed timing** (positive worst-case slack) and I successfully created
  an **Amazon FPGA Image**: **`afi-0a02e7745a17daa6a`** (`agfi-006b5fd42e5f1cb6e`),
  state **available**.
- All I need now is to launch **a single `f2.6xlarge`** to load this AFI and run short
  validation passes (load image, stream test data, read on-chip latency counters) —
  typically under an hour per session, then stop/terminate.

**Specifics:**
- Instance type: one `f2.6xlarge` (24 vCPUs, 1 FPGA)
- **Why 24 vCPUs:** 24 is simply the size of a single `f2.6xlarge` — the **smallest** F2
  instance available. I cannot request less; there is no smaller F2. This is the minimum
  needed for a one-FPGA validation run, not a request to scale.
- Region: US East (N. Virginia) — where the F2 family, the HDK shell, and my existing
  AFI/S3 artifacts already reside
- Timeline: immediate; no expectation of scaling beyond one instance
- The account is on a paid plan with a valid payment method.

I'd be grateful for any review that would enable F-instance capacity, given there is
already a built, validated AFI ready to run. Happy to provide any further detail.

Thank you,
Abhishek
abhivarma1028@gmail.com

---

## Short version (for the Sales web form's small text box)

**Subject:** Enable EC2 F-instance (FPGA) access — account 546517269032

Hello — I'm requesting an account review to enable EC2 **F-instance (FPGA)** access
in **us-east-1**. My Service Quotas request for "Running On-Demand F instances"
(24 vCPUs, one `f2.6xlarge`) was rejected, and I was advised to contact my account team.

I'm an FPGA engineer running a personal R&D project (a SystemVerilog "tick-to-trade"
market-data accelerator). I've **already completed the full AWS FPGA build** on the
FPGA Developer AMI and have a validated, available **AFI: `afi-0a02e7745a17daa6a`**.
I just need **one `f2.6xlarge`** to load it and run short validation passes (under an
hour each, then terminate). The 24-vCPU limit I'm asking for is exactly one
`f2.6xlarge` — the **smallest** F2 instance; I can't request less. Account is on a paid
plan with a valid payment method.

Given there's already a built AFI ready to run, I'd appreciate a review to enable
F-instance capacity. Happy to provide more detail.

Thank you,
Abhishek — abhivarma1028@gmail.com
