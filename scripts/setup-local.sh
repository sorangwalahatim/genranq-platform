#!/usr/bin/env bash
# One-time local setup: writes .env with fresh secrets, then starts GENRANQ on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  cp .env.example .env
  JWT=$(python3 -c "import secrets;print(secrets.token_urlsafe(64))" 2>/dev/null || openssl rand -base64 64 | tr -d '\n')
  KEK=$(python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())" 2>/dev/null || openssl rand -base64 32)
  sed -i.bak -e "s|^JWT_SECRET=.*|JWT_SECRET=$JWT|" -e "s|^VAULT_MASTER_KEY=.*|VAULT_MASTER_KEY=$KEK|" .env && rm -f .env.bak
  echo "Created .env with new secrets (keep VAULT_MASTER_KEY safe — it decrypts stored credentials)."
fi
docker compose up -d --build
echo "Waiting for GENRANQ to start…"
for i in $(seq 1 90); do
  if curl -fs http://localhost:8000/api/health >/dev/null 2>&1; then echo "Ready: open http://localhost:8000 and click Sign up"; exit 0; fi
  sleep 2
done
echo "Still starting — check logs with: docker compose logs -f api"
