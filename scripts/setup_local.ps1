$ErrorActionPreference = "Stop"

$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
if (!(Test-Path $psql)) {
  throw "psql introuvable: $psql. Ajuste le chemin si ta version PostgreSQL est différente."
}

$svc = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$svc -or $svc.Status -ne "Running") {
  throw "Le service PostgreSQL ne semble pas démarré. Démarre-le puis relance."
}

if (-not $env:PGPASSWORD -or $env:PGPASSWORD.Trim().Length -eq 0) {
  throw "PGPASSWORD n'est pas défini. Fais: `$env:PGPASSWORD='MOT_DE_PASSE_POSTGRES' puis relance."
}

$env:PGCONNECT_TIMEOUT = "3"

Write-Host "Création role/db si nécessaire..."

# ROLE (ignore si existe)
& $psql -h localhost -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'les_delices') THEN CREATE ROLE les_delices LOGIN PASSWORD 'les_delices'; END IF; END $$;"

# DB: CREATE DATABASE ne peut pas être exécuté dans une transaction (DO bloque)
$dbExists = & $psql -h localhost -p 5432 -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='les_delices';"
if ($dbExists.Trim() -ne "1") {
  & $psql -h localhost -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE les_delices OWNER les_delices;"
}

Write-Host "OK: role=db prêts."
