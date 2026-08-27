# AWS Support Case — F-Instance (FPGA) Limit → 24 vCPU  ·  COPY-PASTE PACKAGE

**Account:** 546517269032   **Region:** us-east-1 (N. Virginia)
**Where to file:** Support Center → Create case → **"Account"** (free on all tiers)
https://support.console.aws.amazon.com/support/home#/case/create
Category: Account → Subject/body below → attach the files in the "Attachments" section.

---

## SUBJECT (copy this line)
Enable EC2 F-instance (FPGA) access — raise "Running On-Demand F instances" to 24, account 546517269032

## BODY (copy everything between the lines — plain text, no markdown)
------------------------------------------------------------------------------
Hello,

I'm requesting an account review to enable EC2 F-instance (FPGA) capacity in US East (N. Virginia) on account 546517269032, by raising the quota "Running On-Demand F instances" from its current 8 vCPUs to 24 vCPUs. Two prior Service Quotas requests (request IDs on this account) were closed at 8 without a stated reason. The Service Quotas form has no field to explain the use case, so I'm opening this case to provide the justification directly.

What I'm doing: I'm an FPGA hardware engineer running a personal R&D / learning project — a low-latency "tick-to-trade" market-data accelerator written in SystemVerilog (ITCH market-data parser -> limit order book -> trading-signal logic -> pre-trade risk checks -> order emission). This is for hardware benchmarking and education, not a production or commercial trading operation.

Why this is a legitimate, low-risk request — the work is already done on AWS:
I have already completed the entire AWS FPGA build flow on this account:
- Synthesized, placed, and routed the design against the Virtex UltraScale+ xcvu47p (F2 target) on a z1d.2xlarge running the AWS FPGA Developer AMI, using the AWS HDK.
- The design closed timing (positive worst-case slack) and I successfully created an Amazon FPGA Image that is in the available state on this account:
  - AFI: afi-0a02e7745a17daa6a
  - AGFI: agfi-006b5fd42e5f1cb6e
  - Name hft-tick-to-trade, Shell 0x10212415, Owner 546517269032.
- You can verify this directly against my account — AWS's own AFI backend validated and baked this image. Someone abusing capacity does not run a full HDK synthesis + place-and-route + AFI ingestion; this represents weeks of genuine engineering.

Why exactly 24 vCPUs (this removes the "why so much?" question):
24 is not an arbitrary amount — it is the size of a single f2.6xlarge, which is the smallest F2 instance AWS offers (24 vCPU / 1 FPGA). I cannot request less; there is no smaller F2. f2.12xlarge (48) and f2.48xlarge (192) are larger than I need. So 24 = the minimum viable request to run one FPGA. I am not asking to scale.

Exactly what I need it for and for how long:
Launch one f2.6xlarge, load the AFI above, stream recorded test data, and read the on-chip latency counters to validate the design on real silicon — typically under an hour per session, then stop/terminate. No persistent or always-on workload.

Account standing: paid plan with a valid payment method on file. I've attached project documentation (architecture, the AWS F2 build flow, and results) in case it's helpful for the review.

I'd be grateful for a review to raise the F-instance limit to 24 vCPUs, given there is already a built, validated, available AFI on this account ready to run.

Thank you,
Abhishek
abhivarma1028@gmail.com
------------------------------------------------------------------------------

---

## ATTACHMENTS (upload these with the case — each backs a claim)
Attach from the repo `docs/` folder. Keep to 2–3; reviewers skim.

| File | What it proves | Priority |
|---|---|---|
| `docs/12_aws_f2_cloud_build.pdf` | The exact AWS F2 HDK build flow I ran (z1d + Dev AMI → DCP → AFI). Directly matches the case. | **Attach — must** |
| `docs/01_hft_project_portfolio.pdf` | One-doc overview: the design is a real, coherent engineering project. | **Attach — must** |
| `docs/02_executive_summary.pdf` | Short summary of design + measured results, if a 3rd is wanted. | Optional |
| `docs/08_implementation_results.pdf` | Detailed timing/area/power reports (heavier; only if they ask for depth). | Only if requested |

## SELF-VERIFIABLE EVIDENCE (reviewer can run against account 546517269032)
```
aws ec2 describe-fpga-images --fpga-image-ids afi-0a02e7745a17daa6a --region us-east-1
# → State: available | AGFI: agfi-006b5fd42e5f1cb6e | Owner: 546517269032 | Shell: 0x10212415
```
This is stronger than any attachment: it's AWS's own record that a legitimate FPGA build
already succeeded on this account. Mention it explicitly (done in the body above).

## IF THEY REPLY ASKING FOR MORE
- Reaffirm: single f2.6xlarge, <1 hr sessions, terminate after, no scaling.
- Offer to describe the design pipeline in more detail (ITCH → book → strategy → risk).
- Point again to the available AFI as proof of completed, non-abusive work.
