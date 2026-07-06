#!/bin/bash
set -e

# Export environment variables to /etc/environment so the cron job can access them
printenv | grep -v "no_proxy" >> /etc/environment

echo "[Docker] Environment loaded. Starting Win-Back cron..."
service cron start

# Stream the cron log to standard output so `docker compose logs` can capture it
exec tail -f /var/log/cron.log
