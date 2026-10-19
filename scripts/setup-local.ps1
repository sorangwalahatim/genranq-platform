# One-time local setup for Windows (PowerShell): writes .env with fresh secrets, then starts GENRANQ on http://localhost:8000
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .env)) {
  $bytes = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $jwt = [Convert]::ToBase64String($bytes)
  $k = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($k)
  $kek = [Convert]::ToBase64String($k)
  (Get-Content .env.example) -replace '^JWT_SECRET=.*', "JWT_SECRET=$jwt" -replace '^VAULT_MASTER_KEY=.*', "VAULT_MASTER_KEY=$kek" | Set-Content .env -Encoding ascii
  Write-Host "Created .env with new secrets (keep VAULT_MASTER_KEY safe - it decrypts stored credentials)."
}
docker compose up -d --build
Write-Host "Waiting for GENRANQ to start..."
for ($i = 0; $i -lt 90; $i++) {
  try { Invoke-WebRequest -UseBasicParsing http://localhost:8000/api/health | Out-Null; Write-Host "Ready: open http://localhost:8000 and click Sign up"; Start-Process "http://localhost:8000"; exit 0 } catch { Start-Sleep 2 }
}
Write-Host "Still starting - check logs with: docker compose logs -f api"
