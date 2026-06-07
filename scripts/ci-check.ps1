$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

Write-Host "==> Installing dependencies..."
& $python -m pip install --upgrade pip -q
& $python -m pip install -r requirements.txt -r requirements-dev.txt -q

Write-Host "==> Running tests..."
$env:SECRET_KEY = "ci-test-secret-key-not-for-production"
$env:DATABASE_URL = "sqlite:///:memory:"
$env:CORS_ORIGINS = '["https://launchtrendai.vercel.app"]'
& $python -m pytest tests/ -v

Write-Host "All checks passed. Safe to push."
