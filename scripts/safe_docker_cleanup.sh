#!/usr/bin/env bash
set -Eeuo pipefail

cd /root/legacy-model

echo "============================================================"
echo "SAFE DOCKER DISK CLEANUP"
echo "============================================================"
echo "This keeps Docker volumes, so PostgreSQL data is preserved."
echo "It removes stopped containers, dangling build cache, unused images,"
echo "old deployment logs, and compresses old SQL backups."
echo "============================================================"

echo
echo "1. Stop worker only"
docker compose stop worker >/dev/null 2>&1 || true

echo
echo "2. Disk usage before cleanup"
df -h /
docker system df || true

echo
echo "3. Remove exited one-off containers"
docker container prune -f || true

echo
echo "4. Remove Docker build cache"
docker builder prune -af || true

echo
echo "5. Remove unused images, networks, and cache WITHOUT volumes"
docker system prune -af || true

echo
echo "6. Compress old SQL backups, keep latest two raw backup folders"
if [ -d /root/legacy-model-backups ]; then
  find /root/legacy-model-backups -type f -name '*.sql' -size +1M -mtime +0 -print0 \
    | xargs -0 -r gzip -9 || true
  ls -1dt /root/legacy-model-backups/pre-hybrid-v3-* 2>/dev/null \
    | tail -n +4 \
    | while read -r olddir; do
        echo "Archiving old backup directory: $olddir"
        tar -C "$(dirname "$olddir")" -czf "${olddir}.tar.gz" "$(basename "$olddir")" \
          && rm -rf "$olddir" || true
      done
fi

echo
echo "7. Remove old deployment logs, keep latest five"
ls -1t /root/hybrid-v3-deployment-*.log 2>/dev/null \
  | tail -n +6 \
  | xargs -r rm -f || true

echo
echo "8. Disk usage after cleanup"
df -h /
docker system df || true

echo
echo "============================================================"
echo "SAFE CLEANUP COMPLETE"
echo "PostgreSQL Docker volumes were NOT pruned."
echo "============================================================"
