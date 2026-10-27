#!/usr/bin/env bash
set -euo pipefail

# Replace the BirdFrame container on a Docker host with a new image.
#
#   scripts/deploy.sh           pull the published image of this checkout's
#                               commit (ghcr.io/bertrandom/birdframe:sha-<commit>)
#   scripts/deploy.sh --local   build this checkout here, including uncommitted
#                               changes, and copy the image over SSH
#
# Settings come from the environment; the defaults match the install on nuke:
#   DEPLOY_HOST       SSH host running Docker                  (nuke)
#   REGISTRY_IMAGE    published image                          (ghcr.io/bertrandom/birdframe)
#   IMAGE_TAG         published tag to deploy, e.g. latest     (sha-<HEAD commit>)
#   DEPLOY_PLATFORM   platform of that host, for --local       (linux/amd64)
#   CONTAINER         container name                           (birdframe)
#   DATA_DIR          data directory on the host               (/web/birdframe/data)
#   PORT              host port for the web UI and API         (8765)
#   TZ_NAME           container timezone                       (America/Los_Angeles)
#   KEEP_BACKUPS      data backups to keep on the host         (3)
#
# Before switching, the data directory is copied to <DATA_DIR>-backup-<time> while
# the old container is stopped, and the old container is kept as
# <CONTAINER>-previous. If the new container doesn't report healthy, the script
# restores that backup and starts the previous container again.

DEPLOY_HOST=${DEPLOY_HOST:-nuke}
REGISTRY_IMAGE=${REGISTRY_IMAGE:-ghcr.io/bertrandom/birdframe}
DEPLOY_PLATFORM=${DEPLOY_PLATFORM:-linux/amd64}
CONTAINER=${CONTAINER:-birdframe}
DATA_DIR=${DATA_DIR:-/web/birdframe/data}
PORT=${PORT:-8765}
TZ_NAME=${TZ_NAME:-America/Los_Angeles}
KEEP_BACKUPS=${KEEP_BACKUPS:-3}

cd "$(dirname "$0")/.."

commit=$(git rev-parse HEAD | cut -c1-7)

case "${1:-}" in
  --local)
    revision=$commit
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
      revision="$revision-dirty"
      echo "Warning: uncommitted changes will be included in birdframe:$revision" >&2
    fi
    image="birdframe:$revision"

    echo "==> Building $image for $DEPLOY_PLATFORM"
    docker buildx build --platform "$DEPLOY_PLATFORM" -t "$image" --load .

    echo "==> Copying $image to $DEPLOY_HOST"
    docker save "$image" | gzip -1 | ssh "$DEPLOY_HOST" 'gunzip | docker load'
    ;;
  "")
    if [ -z "${IMAGE_TAG:-}" ]; then
      # The publish workflow tags each pushed commit as sha-<first 7 characters>.
      if [ -z "$(git branch -r --contains HEAD 2>/dev/null)" ]; then
        echo "Commit $commit isn't pushed, so it has no published image." >&2
        echo "Push it and wait for the Publish image workflow, or use --local." >&2
        exit 1
      fi
      IMAGE_TAG="sha-$commit"
    fi
    image="$REGISTRY_IMAGE:$IMAGE_TAG"

    echo "==> Pulling $image on $DEPLOY_HOST"
    if ! ssh "$DEPLOY_HOST" docker pull --quiet "$image"; then
      echo "Could not pull $image. If the commit was just pushed, the Publish" >&2
      echo "image workflow may still be running: gh run list --workflow publish.yml" >&2
      exit 1
    fi
    ;;
  *)
    echo "Usage: scripts/deploy.sh [--local]" >&2
    exit 2
    ;;
esac

echo "==> Replacing $CONTAINER on $DEPLOY_HOST"
# The remote side gets its settings as positional arguments, so nothing in them
# is interpreted by the remote shell.
ssh "$DEPLOY_HOST" bash -s -- "$image" "$CONTAINER" "$DATA_DIR" "$PORT" "$TZ_NAME" "$KEEP_BACKUPS" <<'REMOTE'
set -euo pipefail
image=$1 container=$2 data_dir=$3 port=$4 tz_name=$5 keep_backups=$6
previous="$container-previous"
backup="$data_dir-backup-$(date +%Y%m%d-%H%M%S)"

run_container() {
  docker run -d --name "$1" --restart unless-stopped \
    -p "$port:8765" -v "$data_dir:/data" -e "TZ=$tz_name" "$2" >/dev/null
}

healthy() {
  for _ in $(seq 45); do
    if curl -sf -m 3 "http://127.0.0.1:$port/api/v1/health" | grep -q '"status":"ok"'; then
      return 0
    fi
    sleep 2
  done
  return 1
}

had_container=false
if docker container inspect "$container" >/dev/null 2>&1; then
  had_container=true
  docker stop "$container" >/dev/null
fi

# Copy the data while nothing is writing to it.
cp -a "$data_dir" "$backup"
echo "Backed up $data_dir to $backup"

if $had_container; then
  docker rm -f "$previous" >/dev/null 2>&1 || true
  docker rename "$container" "$previous"
  docker update --restart=no "$previous" >/dev/null
fi

run_container "$container" "$image"
if healthy; then
  echo "$container is healthy on $image"
  docker logs "$container" 2>&1 | grep -v "GET /api/v1/health" | tail -5
else
  echo "$container did not become healthy; rolling back" >&2
  docker logs "$container" 2>&1 | tail -20 >&2
  docker rm -f "$container" >/dev/null
  # The new version may already have migrated the data, so put the backup back
  # and keep the failed copy for inspection.
  failed="$data_dir-failed-$(date +%Y%m%d-%H%M%S)"
  mv "$data_dir" "$failed"
  cp -a "$backup" "$data_dir"
  echo "Restored $backup; the failed data is in $failed" >&2
  if $had_container; then
    docker rename "$previous" "$container"
    docker update --restart=unless-stopped "$container" >/dev/null
    docker start "$container" >/dev/null
    echo "Restarted the previous container" >&2
  fi
  exit 1
fi

# Keep only the newest backups this script made (names sort by time). Backups
# named any other way, e.g. by hand, are left alone.
stamp='[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]'
# shellcheck disable=SC2086 # $stamp is a glob on purpose
{ ls -d "$data_dir"-backup-$stamp 2>/dev/null || true; } | sort | head -n "-$keep_backups" | while read -r old; do
  rm -rf "$old"
  echo "Removed old backup $old"
done
REMOTE

echo "==> Deployed $image to $DEPLOY_HOST:$PORT"
