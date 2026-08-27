#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# launch_f2_run.sh — run from the LOCAL VM once the F-instance quota is granted.
# Launches an f2.6xlarge, loads the AFI, copies the host harness over, drives the
# pipeline on real silicon, prints the result, then leaves the instance RUNNING
# (you decide to stop/terminate — see the reminder at the end).
#
# REGION-AWARE. Default targets us-west-2 (the active quota attempt); override any
# value via env, e.g.:  REGION=us-east-1 ./launch_f2_run.sh
#
# Cost: f2.6xlarge ~$1.65/hr. Keep the session short; terminate when done.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REGION=${REGION:-us-west-2}
TYPE=${TYPE:-f2.6xlarge}
AGFI=${AGFI:-agfi-006b5fd42e5f1cb6e}      # same global id in us-east-1 and us-west-2
REPO=${REPO:-/home/abhishek/Projects/HFT}

# region-specific defaults (FPGA Developer AMI Ubuntu 1.19.2 + an AZ that offers F2)
case "$REGION" in
  us-east-1) AMI=${AMI:-ami-017bd23ff95264395}; AZ=${AZ:-us-east-1a}
             KEY=${KEY:-hft-fpga-build};        SG=${SG:-sg-0811f63df10aab405} ;;
  us-west-2) AMI=${AMI:-ami-07a164f1a402ab274}; AZ=${AZ:-us-west-2a}
             KEY=${KEY:-hft-fpga-build-usw2};   SG=${SG:-}      ;;   # must exist in-region
  *)         : "${AMI:?set AMI}"; : "${AZ:?set AZ}"; : "${KEY:?set KEY}"; : "${SG:?set SG}" ;;
esac
KEYFILE=${KEYFILE:-~/.ssh/${KEY}.pem}

echo "== region=$REGION  type=$TYPE  ami=$AMI  az=$AZ =="

# ── preflight: fail fast with clear guidance instead of a cryptic AWS error ──
q=$(aws service-quotas get-service-quota --service-code ec2 --quota-code L-74FC7D96 \
      --region "$REGION" --query 'Quota.Value' --output text 2>/dev/null || echo 0)
q=${q%.*}
if [ "${q:-0}" -lt 24 ]; then
  echo "ABORT: F-instance vCPU quota in $REGION is ${q:-0} (<24). The f2.6xlarge needs 24."
  echo "       Wait for the quota request to be GRANTED before launching."
  exit 1
fi
if ! aws ec2 describe-key-pairs --key-names "$KEY" --region "$REGION" >/dev/null 2>&1; then
  echo "ABORT: key pair '$KEY' not found in $REGION. Create it (regional):"
  echo "  aws ec2 create-key-pair --region $REGION --key-name $KEY \\"
  echo "    --query KeyMaterial --output text > $KEYFILE && chmod 400 $KEYFILE"
  exit 1
fi
if [ -z "${SG:-}" ] || ! aws ec2 describe-security-groups --group-ids "$SG" --region "$REGION" >/dev/null 2>&1; then
  echo "ABORT: security group '$SG' not found in $REGION. Create one allowing SSH/22 from your IP,"
  echo "       then re-run with SG=<sg-id> (security groups are regional)."
  exit 1
fi
aws ec2 describe-fpga-images --fpga-image-ids "${AGFI/agfi-/afi-}" --region "$REGION" \
  >/dev/null 2>&1 || echo "WARN: could not confirm the AFI in $REGION (check it is 'available')."

SUBNET=$(aws ec2 describe-subnets --region "$REGION" \
  --filters Name=default-for-az,Values=true Name=availability-zone,Values="$AZ" \
  --query 'Subnets[0].SubnetId' --output text)

echo "== launching $TYPE in $AZ (subnet $SUBNET) =="
IID=$(aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" --instance-type "$TYPE" --key-name "$KEY" \
  --security-group-ids "$SG" --subnet-id "$SUBNET" \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":200,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=hft-f2-run},{Key=project,Value=HFT}]' \
  --query 'Instances[0].InstanceId' --output text)
echo "   instance: $IID"

aws ec2 wait instance-running --instance-ids "$IID" --region "$REGION"
IP=$(aws ec2 describe-instances --instance-ids "$IID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "   public IP: $IP"

echo "== waiting for SSH =="
for i in $(seq 1 30); do
  if ssh -i "$KEYFILE" -o StrictHostKeyChecking=no -o ConnectTimeout=8 -o BatchMode=yes \
       ubuntu@"$IP" 'echo ok' >/dev/null 2>&1; then break; fi
  sleep 10
done

echo "== copy host harness (f1/ sim/ host/) =="
rsync -az -e "ssh -i $KEYFILE -o StrictHostKeyChecking=no" \
  "$REPO/f1" "$REPO/sim" "$REPO/host" "$REPO/f2/run_on_f2.sh" \
  ubuntu@"$IP":/home/ubuntu/HFT/
ssh -i "$KEYFILE" -o StrictHostKeyChecking=no ubuntu@"$IP" 'chmod +x /home/ubuntu/HFT/run_on_f2.sh'

echo "== run on silicon =="
ssh -i "$KEYFILE" -o StrictHostKeyChecking=no ubuntu@"$IP" \
  "cd /home/ubuntu/HFT && sudo ./run_on_f2.sh $AGFI 120 7"

echo
echo "==================================================================="
echo " f2 instance $IID ($IP) in $REGION is STILL RUNNING (~\$1.65/hr)."
echo " Stop:      aws ec2 stop-instances --instance-ids $IID --region $REGION"
echo " Terminate: aws ec2 terminate-instances --instance-ids $IID --region $REGION"
echo "==================================================================="
