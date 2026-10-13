# Run the hosted version on this computer for testing (no Docker needed).
#   .\hosted\dev.ps1                 start the web app at http://127.0.0.1:8000
#   .\hosted\dev.ps1 manage users    run an admin command (see server/app/manage.py)
#   .\hosted\dev.ps1 job weather     run a nightly job once (futures, bids, weather)
# Data lives in hosted\devdata\run (not committed). Sign-in links print in this window.
# Snowflake jobs use your own key-pair login from %USERPROFILE%\.snowflake (on the server: a service user).
param([string]$what = 'web', [Parameter(ValueFromRemainingArguments)] [string[]]$rest)
$here = $PSScriptRoot
$py = Join-Path $here '.venv\Scripts\python.exe'
$env:DATA_DIR = Join-Path $here 'devdata\run'
$env:APP_HTML = Join-Path (Split-Path $here -Parent) 'grain-desk.html'
$env:PUBLIC_URL = 'http://127.0.0.1:8000'
$env:SNOWFLAKE_ACCOUNT = 'JSA-ANALYTICS'
$env:SNOWFLAKE_USER = 'MBROTT@JPSI.COM'
$env:SNOWFLAKE_PRIVATE_KEY_PATH = Join-Path $env:USERPROFILE '.snowflake\keys\rsa_key.p8'
$env:SNOWFLAKE_ROLE = 'GRAIN_MARKETING_ROLE'
$env:SNOWFLAKE_WAREHOUSE = 'COMPUTE_WH'
Push-Location (Join-Path $here 'server')
try {
  switch ($what) {
    'web'    { & $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 }
    'manage' { & $py -m app.manage @rest }
    'job'    { & $py -m app.jobs.scheduler @rest }
  }
} finally { Pop-Location }
