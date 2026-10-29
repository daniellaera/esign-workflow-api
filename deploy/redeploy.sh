#!/usr/bin/env bash
# Redeploy the public demo from origin/main. Run on the demo host.
set -euo pipefail

cd "$(dirname "$0")/.."
test -f deploy/demo.env || { echo "deploy/demo.env is missing (see demo.env.example)" >&2; exit 1; }

git fetch --quiet origin main
git reset --hard --quiet origin/main
echo "deploying $(git log -1 --format='%h %s')"

docker compose -f deploy/compose.demo.yml up --detach --build --wait --remove-orphans
docker image prune --force > /dev/null
docker compose -f deploy/compose.demo.yml ps
