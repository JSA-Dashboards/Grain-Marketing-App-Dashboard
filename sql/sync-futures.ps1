# Pull the latest CME corn, soybean and wheat futures from JSA's Snowflake data and
# write them as the app's market/futures document (JSON) for upload to the artifact.
# Usage:  .\sql\sync-futures.ps1     -> writes sync\market-futures.json and prints a summary
# Source: JSA.BASIS_TRACKER.FUTURES_PRICES (DATE, SYMBOL, PRICE_CENTS, CAPTURED_AT), loaded daily.

$snow = "C:\Program Files\Snowflake CLI\snow.exe"
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path $PSScriptRoot -Parent
$outDir = Join-Path $root 'sync'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# latest two trading dates; last capture per symbol; outrights only (ZCZ26, ZSX26, ZWZ26 ...)
$q = @"
WITH d AS (SELECT DISTINCT DATE FROM JSA.BASIS_TRACKER.FUTURES_PRICES
           WHERE LEFT(SYMBOL,2) IN ('ZC','ZS','ZW') ORDER BY DATE DESC LIMIT 2)
SELECT DATE, SYMBOL, PRICE_CENTS
FROM JSA.BASIS_TRACKER.FUTURES_PRICES
WHERE DATE IN (SELECT DATE FROM d) AND LEFT(SYMBOL,2) IN ('ZC','ZS','ZW') AND LENGTH(SYMBOL) = 5
QUALIFY ROW_NUMBER() OVER (PARTITION BY DATE, SYMBOL ORDER BY CAPTURED_AT DESC) = 1
"@
$raw = & $snow sql -c jsa --format json -q $q 2>$null
if ($LASTEXITCODE -ne 0 -or -not $raw) { throw "Snowflake query failed" }
$rows = ($raw -join "`n") | ConvertFrom-Json
if (-not $rows -or $rows.Count -eq 0) { throw "No futures rows returned" }

$dates = $rows | ForEach-Object { [string]$_.DATE } | Sort-Object -Unique -Descending
$asOf = $dates[0]; $prevDate = if ($dates.Count -gt 1) { $dates[1] } else { $null }
$prices = [ordered]@{}; $prev = [ordered]@{}
foreach ($r in ($rows | Sort-Object SYMBOL)) {
  $usd = [math]::Round([double]$r.PRICE_CENTS / 100, 4)
  if ([string]$r.DATE -eq $asOf) { $prices[$r.SYMBOL] = $usd } elseif ([string]$r.DATE -eq $prevDate) { $prev[$r.SYMBOL] = $usd }
}
$doc = [ordered]@{
  asOf = $asOf; prevDate = $prevDate; syncedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
  source = 'JSA.BASIS_TRACKER.FUTURES_PRICES'; prices = $prices; prev = $prev
}
$out = Join-Path $outDir 'market-futures.json'
# write UTF-8 without BOM so the JSON uploads cleanly
[System.IO.File]::WriteAllText($out, ($doc | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding $false))
"asOf=$asOf prev=$prevDate contracts=$($prices.Count) file=$out"
