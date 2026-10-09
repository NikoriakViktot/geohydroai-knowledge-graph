#!/usr/bin/env bash
# Publish a catalog snapshot from the workstation to the server, atomically.
#
#   deploy/catalog/publish_catalog.sh data/exports/catalog_20261009_catalog-rules-v1 catalog@server
#   deploy/catalog/publish_catalog.sh --rollback catalog@server        # back to the previous release
#
# Copies the snapshot into /srv/catalog/releases/<name>/, checks every sha256 against manifest.json
# on the server, switches the /srv/catalog/current symlink, restarts the service and checks /health.
# The previous release stays on disk; nothing is overwritten (a release name is never reused).
set -euo pipefail

REMOTE_ROOT=/srv/catalog

if [[ "${1:-}" == "--rollback" ]]; then
    host=${2:?usage: --rollback user@host}
    ssh "$host" bash -s <<REMOTE
set -euo pipefail
cd $REMOTE_ROOT
cur=\$(readlink current)
# the release published just before the current one (names start with the build date)
prev=\$(ls -1d releases/*/ | sed 's#/\$##' | grep -v '\.part\$' | sort | awk -v c="\$cur" '\$0==c{print p; exit} {p=\$0}')
[[ -n "\$prev" ]] || { echo "no previous release"; exit 1; }
ln -sfn "\$prev" current.tmp && mv -T current.tmp current
sudo systemctl restart catalog-api
sleep 2; curl -fsS http://127.0.0.1:8095/health; echo
echo "rolled back: \$cur -> \$prev"
REMOTE
    exit 0
fi

snap=${1:?usage: publish_catalog.sh <snapshot_dir> user@host}
host=${2:?usage: publish_catalog.sh <snapshot_dir> user@host}
name=$(basename "$snap")
[[ -f "$snap/manifest.json" && -f "$snap/cards.jsonl" ]] || { echo "$snap is not a catalog snapshot"; exit 1; }

ssh "$host" "test ! -e $REMOTE_ROOT/releases/$name" || { echo "release $name already on the server"; exit 1; }
# hidden.jsonl / excluded.jsonl are audit files: copied too, the API reads only cards + manifest
rsync -a --chmod=D755,F644 "$snap/" "$host:$REMOTE_ROOT/releases/$name.part/"

ssh "$host" bash -s <<REMOTE
set -euo pipefail
cd $REMOTE_ROOT/releases/$name.part
python3 - <<'PY'
import hashlib, json, sys
m = json.load(open("manifest.json"))
for f, want in m["files"].items():
    got = hashlib.sha256(open(f, "rb").read()).hexdigest()
    if got != want:
        sys.exit(f"{f}: sha256 mismatch")
print("sha256 ok:", ", ".join(m["files"]), "| cards", m["counts"]["cards"], "| rules", m["rules_version"])
PY
cd $REMOTE_ROOT
mv releases/$name.part releases/$name
ln -sfn releases/$name current.tmp && mv -T current.tmp current
sudo systemctl restart catalog-api
sleep 2
curl -fsS http://127.0.0.1:8095/health; echo
REMOTE
echo "published $name to $host"
