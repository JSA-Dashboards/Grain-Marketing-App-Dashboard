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

# RMA crop insurance prices for corn and soybeans (spring-planted, Midwest price-discovery windows):
# projected = average of February settles, harvest = average of October settles, of December corn / November soybeans.
# Rounded to the cent like RMA. "final" once the month is over; before that it is the average so far.
$yr = (Get-Date).Year; $today = (Get-Date).ToString('yyyy-MM-dd')
# every crop year back to 2008 (the price-check history table); daily settles for the last two years and next
$rmaSyms = @(2008..($yr + 1) | ForEach-Object { $yy = '{0:d2}' -f ($_ % 100); "'ZCZ$yy'"; "'ZSX$yy'" }) -join ','
$rmaRows = Invoke-Snow @"
WITH s AS (SELECT DATE, SYMBOL, PRICE_CENTS FROM JSA.BASIS_TRACKER.FUTURES_PRICES WHERE SYMBOL IN ($rmaSyms)
           QUALIFY ROW_NUMBER() OVER (PARTITION BY DATE, SYMBOL ORDER BY CAPTURED_AT DESC) = 1)
SELECT SYMBOL, 2000 + RIGHT(SYMBOL,2)::INT AS YR,
  AVG(IFF(LEFT(DATE,7) = '20' || RIGHT(SYMBOL,2) || '-02', PRICE_CENTS, NULL)) / 100 AS PROJ,
  COUNT(IFF(LEFT(DATE,7) = '20' || RIGHT(SYMBOL,2) || '-02', 1, NULL)) AS PROJ_DAYS,
  AVG(IFF(LEFT(DATE,7) = '20' || RIGHT(SYMBOL,2) || '-10', PRICE_CENTS, NULL)) / 100 AS HARV,
  COUNT(IFF(LEFT(DATE,7) = '20' || RIGHT(SYMBOL,2) || '-10', 1, NULL)) AS HARV_DAYS
FROM s GROUP BY SYMBOL
"@
$rma = [ordered]@{}
foreach ($r in @($rmaRows)) {
  $y = [string]$r.YR; $crop = if ($r.SYMBOL -like 'ZC*') { 'corn' } else { 'soybeans' }
  if (-not $rma.Contains($y)) { $rma[$y] = [ordered]@{} }
  $rma[$y][$crop] = [ordered]@{ symbol = $r.SYMBOL
    proj = if ($r.PROJ -ne $null) { [math]::Round([double]$r.PROJ, 2) } else { $null }; projDays = [int]$r.PROJ_DAYS; projFinal = ($today -ge "$y-03-01")
    harv = if ($r.HARV -ne $null) { [math]::Round([double]$r.HARV, 2) } else { $null }; harvDays = [int]$r.HARV_DAYS; harvFinal = ($today -ge "$y-11-01") }
}

# daily settles in each price-discovery month (February, October) for last year, this year and next, for the Price checks chart
$daySyms = @(($yr - 1)..($yr + 1) | ForEach-Object { $yy = '{0:d2}' -f ($_ % 100); "'ZCZ$yy'"; "'ZSX$yy'" }) -join ','
$dayRows = Invoke-Snow @"
SELECT DATE, SYMBOL, PRICE_CENTS FROM JSA.BASIS_TRACKER.FUTURES_PRICES
WHERE SYMBOL IN ($daySyms) AND LEFT(DATE,7) IN ('20' || RIGHT(SYMBOL,2) || '-02', '20' || RIGHT(SYMBOL,2) || '-10')
QUALIFY ROW_NUMBER() OVER (PARTITION BY DATE, SYMBOL ORDER BY CAPTURED_AT DESC) = 1
ORDER BY SYMBOL, DATE
"@
$rmaDaily = [ordered]@{}
foreach ($r in @($dayRows)) {
  $y = '20' + $r.SYMBOL.Substring(3, 2); $crop = if ($r.SYMBOL -like 'ZC*') { 'corn' } else { 'soybeans' }
  $m = if (([string]$r.DATE).Substring(5, 2) -eq '02') { 'feb' } else { 'oct' }
  if (-not $rmaDaily.Contains($y)) { $rmaDaily[$y] = [ordered]@{} }
  if (-not $rmaDaily[$y].Contains($crop)) { $rmaDaily[$y][$crop] = [ordered]@{ feb = @(); oct = @() } }
  $rmaDaily[$y][$crop][$m] += , @([string]$r.DATE, [math]::Round([double]$r.PRICE_CENTS / 100, 4))
}

$doc = [ordered]@{
  asOf = $asOf; prevDate = $prevDate; syncedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
  source = 'JSA.BASIS_TRACKER.FUTURES_PRICES'; prices = $prices; prev = $prev; hist = $hist; fci = $fciDoc; rma = $rma; rmaDaily = $rmaDaily
}
$out = Join-Path $outDir 'market-futures.json'
[System.IO.File]::WriteAllText($out, ($doc | ConvertTo-Json -Depth 8 -Compress), (New-Object System.Text.UTF8Encoding $false))
"asOf=$asOf prev=$prevDate contracts=$($prices.Count) fci=$(if($fciDoc){$fciDoc.asOf}else{'none'}) rma=$(($rma.Keys | ForEach-Object { $k=$_; "$k corn $($rma[$k].corn.proj)/$($rma[$k].corn.harv) soy $($rma[$k].soybeans.proj)/$($rma[$k].soybeans.harv)" }) -join '; ') bytes=$((Get-Item $out).Length) file=$out"
