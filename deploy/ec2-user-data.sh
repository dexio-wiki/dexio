#!/bin/bash
# EC2 user data: brings dexio up on a fresh Amazon Linux 2023 instance.
# Paste as user data, or pass with `aws ec2 run-instances --user-data file://...`.
#
# Set these before launching:
#   DEXIO_DOMAIN          DNS name already pointed at this instance's IP
#   DEXIO_ADMIN_EMAIL     seeds the first dashboard login
#   DEXIO_ADMIN_PASSWORD  password for that login
#   DATA_DEVICE               EBS device holding the database, default /dev/sdf
#
# t4g.small with 20GB gp3 is enough for a fleet's worth of markdown.
# No -x: this script handles the login password, and cloud-init
# writes every traced command to /var/log/cloud-init-output.log in plaintext.
set -euo pipefail

DEXIO_REPO="${DEXIO_REPO:-https://github.com/dexio-wiki/dexio.git}"
DEXIO_DOMAIN="${DEXIO_DOMAIN:-}"
DEXIO_VIEW_PASSWORD="${DEXIO_VIEW_PASSWORD:-}"
DEXIO_DATA_DIR="${DEXIO_DATA_DIR:-/data}"
DATA_DEVICE="${DATA_DEVICE:-/dev/sdf}"

dnf -y update
dnf -y install docker git e2fsprogs

# --- persistent data volume -------------------------------------------------
# The root disk is destroyed whenever the instance is replaced, so the database
# and Caddy's certificates live on a separate EBS volume that outlives it.
# The filesystem is created only when the device has none: creating one
# unconditionally would erase the data this whole block exists to preserve.
# Claim the data volume. CloudFormation cannot do this during an instance
# replacement, because the outgoing instance still holds the volume when the
# replacement boots and the attach fails with "already attached", rolling the
# whole stack back. Here we can simply wait for it to be released.
attach_data_volume() {
  [ -n "${DATA_VOLUME_ID:-}" ] || return 0
  local token iid state i
  token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
          -H "X-aws-ec2-metadata-token-ttl-seconds: 300" || true)
  iid=$(curl -s -H "X-aws-ec2-metadata-token: ${token}" \
        "http://169.254.169.254/latest/meta-data/instance-id")
  local region
  region=$(curl -s -H "X-aws-ec2-metadata-token: ${token}" \
           "http://169.254.169.254/latest/meta-data/placement/region")

  for i in $(seq 1 60); do
    state=$(aws ec2 describe-volumes --volume-ids "${DATA_VOLUME_ID}" --region "${region}" \
            --query 'Volumes[0].Attachments[0].InstanceId' --output text 2>/dev/null || echo None)
    if [ "${state}" = "${iid}" ]; then
      echo "data volume already attached to this instance"; return 0
    fi
    if [ "${state}" = "None" ] || [ -z "${state}" ]; then
      if aws ec2 attach-volume --volume-id "${DATA_VOLUME_ID}" --instance-id "${iid}" \
           --device "${DATA_DEVICE}" --region "${region}" >/dev/null 2>&1; then
        echo "attached ${DATA_VOLUME_ID}"; sleep 5; return 0
      fi
    else
      echo "waiting for ${state} to release the data volume (${i}/60)"
    fi
    sleep 5
  done
  # Stop here rather than fall through to the root-disk fallback below: with a
  # volume id set, that would start the app on an empty database, show users
  # an empty wiki, and put their writes on a disk the next replacement deletes.
  echo "FATAL: ${DATA_VOLUME_ID} was not released to this instance within 5 minutes"
  exit 1
}

attach_data_volume

resolve_device() {
  local i
  for i in $(seq 1 30); do
    if [ -b "${DATA_DEVICE}" ]; then return 0; fi
    # Nitro instances expose /dev/sdf as some /dev/nvmeXn1; find the attached
    # disk that is neither the root device nor already carrying a partition.
    local d
    for d in /dev/nvme*n1; do
      [ -b "$d" ] || continue
      if [ "$(lsblk -no MOUNTPOINT "$d" | tr -d '[:space:]')" = "" ] \
         && [ "$(lsblk -nro NAME "$d" | wc -l)" = "1" ]; then
        DATA_DEVICE="$d"
        return 0
      fi
    done
    sleep 2
  done
  return 1
}

if resolve_device; then
  if blkid "${DATA_DEVICE}" >/dev/null 2>&1; then
    echo "reusing existing filesystem on ${DATA_DEVICE}"
  else
    echo "creating filesystem on fresh volume ${DATA_DEVICE}"
    /usr/sbin/mke2fs -t ext4 -L dexio "${DATA_DEVICE}"
  fi
  # fstab mounts by label, so the label has to be ours. A volume restored from
  # an older snapshot (the wikiscope -> dexio move) carries the old label; with
  # nofail, `mount` then exits 0 without mounting anything and the database
  # quietly lands on the root disk. Relabelling is metadata only.
  label=$(blkid -s LABEL -o value "${DATA_DEVICE}" 2>/dev/null || true)
  if [ "${label}" != "dexio" ]; then
    echo "relabelling ${DATA_DEVICE} from '${label}' to dexio"
    /usr/sbin/e2label "${DATA_DEVICE}" dexio
    udevadm trigger --action=change "${DATA_DEVICE}" || true
    udevadm settle || true
  fi
  mkdir -p "${DEXIO_DATA_DIR}"
  grep -q "LABEL=dexio" /etc/fstab || \
    echo "LABEL=dexio ${DEXIO_DATA_DIR} ext4 defaults,nofail 0 2" >> /etc/fstab
  mountpoint -q "${DEXIO_DATA_DIR}" || mount "${DEXIO_DATA_DIR}" || true
  # nofail turns a failed mount into success, so check the result. Stopping
  # here beats serving from a database that dies with the instance.
  if ! mountpoint -q "${DEXIO_DATA_DIR}"; then
    echo "FATAL: ${DATA_DEVICE} did not mount at ${DEXIO_DATA_DIR}"
    exit 1
  fi
  df -h "${DEXIO_DATA_DIR}" | tail -1
else
  if [ -n "${DATA_VOLUME_ID:-}" ]; then
    echo "FATAL: ${DATA_VOLUME_ID} is attached but no block device appeared for it"
    exit 1
  fi
  echo "WARNING: no data device found, falling back to the root disk (NOT durable)"
  mkdir -p "${DEXIO_DATA_DIR}"
fi
mkdir -p "${DEXIO_DATA_DIR}/dexio" \
         "${DEXIO_DATA_DIR}/caddy" "${DEXIO_DATA_DIR}/caddy-config"
# The container runs as a non-root user (see deploy/Dockerfile). A docker named
# volume would have been chowned to that user automatically; a bind mount is
# not, so without this SQLite fails with "unable to open database file".
CONTAINER_UID="${CONTAINER_UID:-10001}"
chown -R "${CONTAINER_UID}:${CONTAINER_UID}" "${DEXIO_DATA_DIR}/dexio"

systemctl enable --now docker

DOCKER_COMPOSE_VERSION=v2.32.4
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
  "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-linux-$(uname -m)"
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

install -d -m 0750 /opt/dexio
# The source may already be here: CDK ships it as an S3 asset so the instance
# needs no GitHub access and the repo can stay private. Only clone if it isn't.
if [ ! -d /opt/dexio/src ]; then
  git clone --depth 1 "$DEXIO_REPO" /opt/dexio/src
fi

if [ -z "${DEXIO_IMAGE:-}" ] && [ -s /opt/dexio/src/IMAGE ]; then
  DEXIO_IMAGE=$(cat /opt/dexio/src/IMAGE)
fi
cat > /opt/dexio/src/deploy/.env <<EOF
DEXIO_DOMAIN=${DEXIO_DOMAIN}
DEXIO_ADMIN_EMAIL=${DEXIO_ADMIN_EMAIL:-}
DEXIO_ADMIN_PASSWORD=${DEXIO_ADMIN_PASSWORD:-}
DEXIO_VIEW_PASSWORD=${DEXIO_VIEW_PASSWORD:-}
DEXIO_DATA_DIR=${DEXIO_DATA_DIR}
STRIPE_SECRET_KEY=${STRIPE_SECRET_KEY:-}
STRIPE_WEBHOOK_SECRET=${STRIPE_WEBHOOK_SECRET:-}
STRIPE_PORTAL_CONFIG=${STRIPE_PORTAL_CONFIG:-}
DEXIO_MAIL=${DEXIO_MAIL:-}
DEXIO_DATABASE_URL=${DEXIO_DATABASE_URL:-}
DEXIO_IMAGE=${DEXIO_IMAGE:-}
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}
GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET:-}
GITHUB_CLIENT_ID=${GITHUB_CLIENT_ID:-}
GITHUB_CLIENT_SECRET=${GITHUB_CLIENT_SECRET:-}
EOF
chmod 0600 /opt/dexio/src/deploy/.env

cd /opt/dexio/src/deploy
if [ -n "${DEXIO_IMAGE:-}" ]; then
  # A release built and tested in CI: pull it rather than build on this machine.
  REGISTRY="${DEXIO_IMAGE%%/*}"
  REGION=$(echo "$REGISTRY" | cut -d. -f4)
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
  docker pull "$DEXIO_IMAGE"
  docker compose --env-file .env up -d --no-build
else
  docker compose --env-file .env up -d --build
fi

cat > /etc/systemd/system/dexio.service <<'EOF'
[Unit]
Description=dexio
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/dexio/src/deploy
ExecStart=/usr/bin/docker compose --env-file .env up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOF
systemctl enable dexio.service

echo "dexio is up at https://${DEXIO_DOMAIN}"
echo "data directory: ${DEXIO_DATA_DIR}"
