# Load one "Export for Snowflake" zip from the app into GRAIN_MARKETING.MARKETING.
# Usage:  .\sql\load-export.ps1 "C:\Users\MeganBrott\Downloads\grain-desk-snowflake-2026-10-06.zip"
# Each export is appended as a new snapshot (EXPORTED_AT). Load each zip only once.
param([Parameter(Mandatory=$true)][string]$Zip)

$snow = "C:\Program Files\Snowflake CLI\snow.exe"
$env:PYTHONIOENCODING = 'utf-8'
if (-not (Test-Path $Zip)) { throw "Can't find $Zip" }

$work = Join-Path $env:TEMP ("grain-export-" + [guid]::NewGuid().ToString('N'))
Expand-Archive -Path $Zip -DestinationPath $work
$tables = @{ 'farms.csv'='FARMS'; 'crop_plans.csv'='CROP_PLANS'; 'contracts.csv'='CONTRACTS'; 'loads.csv'='LOADS'; 'pricing_events.csv'='PRICING_EVENTS'; 'bins.csv'='BINS' }
# rolls.csv is in exports from 2026-10-14 on; older zips load without it
if (Test-Path (Join-Path $work 'rolls.csv')) { $tables['rolls.csv'] = 'ROLLS' }
$missing = $tables.Keys | Where-Object { -not (Test-Path (Join-Path $work $_)) }
if ($missing) { Remove-Item $work -Recurse -Force; throw "Not a Grain Marketing Desk export (missing $($missing -join ', '))" }

$put = ($work -replace '\\','/') + '/*.csv'
$copy = ($tables.GetEnumerator() | ForEach-Object {
  "COPY INTO $($_.Value) FROM @GRAIN_DESK_STAGE FILES = ('$($_.Key).gz') FILE_FORMAT = (FORMAT_NAME = GRAIN_DESK_CSV) ON_ERROR = ABORT_STATEMENT FORCE = TRUE;"
}) -join "`n"
$sql = @"
USE SCHEMA GRAIN_MARKETING.MARKETING;
REMOVE @GRAIN_DESK_STAGE;
PUT 'file://$put' @GRAIN_DESK_STAGE AUTO_COMPRESS = TRUE OVERWRITE = TRUE;
$copy
REMOVE @GRAIN_DESK_STAGE;
SELECT 'FARMS' AS tbl, COUNT(*) AS rows_in_latest FROM FARMS_CURRENT UNION ALL
SELECT 'CROP_PLANS', COUNT(*) FROM CROP_PLANS_CURRENT UNION ALL
SELECT 'CONTRACTS', COUNT(*) FROM CONTRACTS_CURRENT UNION ALL
SELECT 'LOADS', COUNT(*) FROM LOADS_CURRENT UNION ALL
SELECT 'PRICING_EVENTS', COUNT(*) FROM PRICING_EVENTS_CURRENT UNION ALL
SELECT 'ROLLS', COUNT(*) FROM ROLLS_CURRENT UNION ALL
SELECT 'BINS', COUNT(*) FROM BINS_CURRENT;
"@
$file = Join-Path $work 'load.sql'
Set-Content -Path $file -Value $sql -Encoding utf8
try { & $snow sql -c jsa -f $file }
finally { Remove-Item $work -Recurse -Force }
