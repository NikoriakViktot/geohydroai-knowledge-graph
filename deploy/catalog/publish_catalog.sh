#!/usr/bin/env bash
# Publish a catalog snapshot from the workstation to the server, atomically.
#
#   deploy/catalog/publish_catalog.sh data/exports/catalog_20261009_catalog-rules-v1 [ssh-host]
#   deploy/catalog/publish_catalog.sh --rollback [ssh-host]       # back to the previous release
#
# ssh-host defaults to "geoai" (~/.ssh/config). Copies the snapshot into
# $CATALOG_HOME/data/releases/<name>/, checks every sha256 against manifest.json on the server,
# switches the data/current symlink, restarts the catalog-api container and checks /health.
# Older releases stay on disk; a release name is never reused.
set -euo pipefail

CATALOG_HOME=${CATALOG_HOME:-/home/ubuntu/geoai-catalog}
DATA=$CATALOG_HOME/data

restart_and_check() {   # runs on the server
    cat <<REMOTE
if ! docker inspect catalog-api >/dev/null 2>&1; then
  echo "snapshot in place; container catalog-api not created yet (docker compose up -d --build)"; exit 0
fi
docker restart catalog-api >/dev/null
for i in \$(seq 1 20); do
  if docker exec catalog-api python -c "import urllib.request,sys; sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:8095/health', timeout=3).read().decode())" 2>/dev/null; then
    echo; exit 0
  fi
  sleep 1
done
echo "catalog-api did not become healthy: docker logs catalog-api"; exit 1
REMOTE
}

if [[ "${1:-}" == "--rollback" ]]; then
    host=${2:-geoai}
    ssh "$host" bash -s <<REMOTE
set -euo pipefail
cd $DATA
cur=\$(readlink current)
# the release published just before the current one (names start with the build date)
prev=\$(ls -1d releases/*/ | sed 's#/\$##' | grep -v '\.part\$' | sort | awk -v c="\$cur" '\$0==c{print p; exit} {p=\$0}')
[[ -n "\$prev" ]] || { echo "no release before \$cur"; exit 1; }
ln -sfn "\$prev" current.tmp && mv -T current.tmp current
echo "rolled back: \$cur -> \$prev"
$(restart_and_check)
REMOTE
    exit 0
fi

snap=${1:?usage: publish_catalog.sh <snapshot_dir> [ssh-host]}
host=${2:-geoai}
snap=${snap%/}
name=$(basename "$snap")
[[ -f "$snap/manifest.json" && -f "$snap/cards.jsonl" ]] || { echo "$snap is not a catalog snapshot"; exit 1; }

ssh "$host" "mkdir -p $DATA/releases && test ! -e $DATA/releases/$name" \
    || { echo "release $name already on $host"; exit 1; }
# hidden.jsonl / excluded.jsonl are audit files: copied too, the API reads only cards + manifest
rsync -a --chmod=D755,F644 "$snap/" "$host:$DATA/releases/$name.part/"

ssh "$host" bash -s <<REMOTE
set -euo pipefail
cd $DATA/releases/$name.part
python3 - <<'PY'
import hashlib, json, sys
m = json.load(open("manifest.json"))
for f, want in m["files"].items():
    if hashlib.sha256(open(f, "rb").read()).hexdigest() != want:
        sys.exit(f"{f}: sha256 mismatch")
print("sha256 ok:", ", ".join(m["files"]), "| cards", m["counts"]["cards"], "| rules", m["rules_version"])
PY
cd $DATA
mv releases/$name.part releases/$name
ln -sfn releases/$name current.tmp && mv -T current.tmp current
$(restart_and_check)
REMOTE
echo "published $name to $host"
