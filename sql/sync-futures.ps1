# Pull the latest CME grain futures (plus recent history for trend lines) and the CME Feeder Cattle
# Index from JSA's Snowflake data, and write the app's market/futures document (JSON) for upload.
# Usage:  .\sql\sync-futures.ps1     -> writes sync\market-futures.json and prints a summary
# Sources: JSA.BASIS_TRACKER.FUTURES_PRICES (DATE, SYMBOL, PRICE_CENTS, CAPTURED_AT), loaded daily;
#          JSA.CME_FEEDER_CATTLE.FCI_DAILY (REPORT_DATE, FCI_VALUE).

$snow = "C:\Program Files\Snowflake CLI\snow.exe"
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path $PSScriptRoot -Parent
$outDir = Join-Path $root 'sync'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function Invoke-Snow($q) {
  $raw = & $snow sql -c jsa --format json -q $q 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $raw) { throw "Snowflake query failed" }
  return ($raw -join "`n") | ConvertFrom-Json
}

# last 25 trading dates; last capture per symbol per day; outrights only (ZCZ26, ZSX26, ZWZ26, KEZ26 ...)
$rows = Invoke-Snow @"
WITH d AS (SELECT DISTINCT DATE FROM JSA.BASIS_TRACKER.FUTURES_PRICES
           WHERE LEFT(SYMBOL,2) IN ('ZC','ZS','ZW','KE') ORDER BY DATE DESC LIMIT 25)
SELECT DATE, SYMBOL, PRICE_CENTS
FROM JSA.BASIS_TRACKER.FUTURES_PRICES
WHERE DATE IN (SELECT DATE FROM d) AND LEFT(SYMBOL,2) IN ('ZC','ZS','ZW','KE') AND LENGTH(SYMBOL) = 5
QUALIFY ROW_NUMBER() OVER (PARTITION BY DATE, SYMBOL ORDER BY CAPTURED_AT DESC) = 1
"@
if (-not $rows -or $rows.Count -eq 0) { throw "No futures rows returned" }

$dates = $rows | ForEach-Object { [string]$_.DATE } | Sort-Object -Unique
$asOf = $dates[-1]; $prevDate = if ($dates.Count -gt 1) { $dates[-2] } else { $null }
$prices = [ordered]@{}; $prev = [ordered]@{}; $hist = [ordered]@{}
foreach ($sym in ($rows | ForEach-Object { $_.SYMBOL } | Sort-Object -Unique)) {
  $series = $rows | Where-Object { $_.SYMBOL -eq $sym } | Sort-Object { [string]$_.DATE }
  $last = $series | Where-Object { [string]$_.DATE -eq $asOf } | Select-Object -First 1
  if (-not $last) { continue }   # contract no longer trading
  $prices[$sym] = [math]::Round([double]$last.PRICE_CENTS / 100, 4)
  $p = $series | Where-Object { [string]$_.DATE -eq $prevDate } | Select-Object -First 1
  if ($p) { $prev[$sym] = [math]::Round([double]$p.PRICE_CENTS / 100, 4) }
  # up to 20 closes, oldest first, for the trend line
  $hist[$sym] = @($series | Select-Object -Last 20 | ForEach-Object { [math]::Round([double]$_.PRICE_CENTS / 100, 4) })
}

# CME Feeder Cattle Index, last 20 reports
$fci = Invoke-Snow "SELECT REPORT_DATE, FCI_VALUE FROM JSA.CME_FEEDER_CATTLE.FCI_DAILY WHERE FCI_VALUE IS NOT NULL ORDER BY REPORT_DATE DESC LIMIT 20"
$fciDoc = $null
if ($fci -and $fci.Count -gt 0) {
  $f = @($fci | Sort-Object { [string]$_.REPORT_DATE })
  $fciDoc = [ordered]@{ asOf = [string]$f[-1].REPORT_DATE; value = [math]::Round([double]$f[-1].FCI_VALUE, 3)
    prev = if ($f.Count -gt 1) { [math]::Round([double]$f[-2].FCI_VALUE, 3) } else { $null }
    hist = @($f | ForEach-Object { [math]::Round([double]$_.FCI_VALUE, 3) }) }
}

$doc = [ordered]@{
  asOf = $asOf; prevDate = $prevDate; syncedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
  source = 'JSA.BASIS_TRACKER.FUTURES_PRICES'; prices = $prices; prev = $prev; hist = $hist; fci = $fciDoc
}
$out = Join-Path $outDir 'market-futures.json'
[System.IO.File]::WriteAllText($out, ($doc | ConvertTo-Json -Depth 5 -Compress), (New-Object System.Text.UTF8Encoding $false))
"asOf=$asOf prev=$prevDate contracts=$($prices.Count) fci=$(if($fciDoc){$fciDoc.asOf}else{'none'}) bytes=$((Get-Item $out).Length) file=$out"
