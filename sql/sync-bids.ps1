# Pull the latest elevator / processor cash bids (basis) for corn, soybeans and wheat from JSA's
# basis tracker and write the app's market/bids document (JSON) for upload.
# Cash price in the app = that day's futures price for the bid's futures symbol + basis.
# Usage:  .\sql\sync-bids.ps1   -> writes sync\market-bids.json and prints a summary
# Sources: JSA.BASIS_TRACKER.SNAPSHOTS / SNAPSHOT_ROWS / LOCATION_META / GRAIN_MAP.

$snow = "C:\Program Files\Snowflake CLI\snow.exe"
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path $PSScriptRoot -Parent
$outDir = Join-Path $root 'sync'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# latest snapshot per provider + location from the last 7 days, with map coordinates
$q = @"
WITH s AS (
  SELECT ID, PROVIDER, LOCATION, TIMESTAMP FROM JSA.BASIS_TRACKER.SNAPSHOTS
  WHERE CREATED_AT >= DATEADD(day, -7, CURRENT_TIMESTAMP())
  QUALIFY ROW_NUMBER() OVER (PARTITION BY PROVIDER, LOCATION ORDER BY ID DESC) = 1)
SELECT s.PROVIDER, s.LOCATION, LEFT(s.TIMESTAMP, 10) AS D, m.STATE, m.LAT, m.LON,
       COALESCE(g.CANONICAL_GRAIN, r.GRAIN) AS GRAIN, g.WHEAT_CLASS, r.DELIVERY_MONTH, r.FUTURES_SYMBOL, r.BASIS_CENTS, r.IS_SPOT
FROM s
JOIN JSA.BASIS_TRACKER.SNAPSHOT_ROWS r ON r.SNAPSHOT_ID = s.ID
JOIN JSA.BASIS_TRACKER.LOCATION_META m ON m.PROVIDER = s.PROVIDER AND m.LOCATION = s.LOCATION
LEFT JOIN JSA.BASIS_TRACKER.GRAIN_MAP g ON g.RAW_GRAIN = r.GRAIN
WHERE m.LAT IS NOT NULL AND m.LON IS NOT NULL AND r.BASIS_CENTS IS NOT NULL AND r.FUTURES_SYMBOL IS NOT NULL
"@
$raw = & $snow sql -c jsa --format json -q $q 2>$null
if ($LASTEXITCODE -ne 0 -or -not $raw) { throw "Snowflake query failed" }
$rows = ($raw -join "`n") | ConvertFrom-Json
if (-not $rows -or $rows.Count -eq 0) { throw "No bid rows returned" }

$months = @{ jan=1; feb=2; mar=3; apr=4; may=5; jun=6; jul=7; aug=8; sep=9; sept=9; oct=10; nov=11; dec=12 }
$now = Get-Date; $curYM = $now.Year * 12 + $now.Month
# "Oct 2026", "OCT 26", "March 2027", "Nov '26" -> year*12+month; spot rows -> this month
function Get-YM($r) {
  if ([int]$r.IS_SPOT -eq 1) { return $curYM }
  $m = [regex]::Match([string]$r.DELIVERY_MONTH, "([A-Za-z]{3,9})\.?\s*'?(\d{2,4})")
  if (-not $m.Success) { return $null }
  $mon = $months[$m.Groups[1].Value.ToLower().Substring(0, [Math]::Min(4, $m.Groups[1].Value.Length))]
  if (-not $mon) { $mon = $months[$m.Groups[1].Value.ToLower().Substring(0, 3)] }
  if (-not $mon) { return $null }
  $y = [int]$m.Groups[2].Value; if ($y -lt 100) { $y += 2000 }
  return $y * 12 + $mon
}
# app grain codes: C corn, S soybeans, W wheat (any class; class kept as a label)
function Get-Code($g) {
  switch -Regex ($g) { '^Corn$' { 'C' } '^Soybeans$' { 'S' } '^(Wheat|Hard Red Spring|Hard Red Winter|Soft Red Winter|Soft White)$' { 'W' } default { $null } }
}

$locIndex = @{}; $locs = New-Object System.Collections.ArrayList; $byKey = @{}
foreach ($r in $rows) {
  $code = Get-Code ([string]$r.GRAIN); if (-not $code) { continue }
  $ym = Get-YM $r; if (-not $ym -or $ym -lt $curYM) { continue }
  $lk = "$($r.PROVIDER)|$($r.LOCATION)"
  if (-not $locIndex.ContainsKey($lk)) {
    $locIndex[$lk] = $locs.Count
    [void]$locs.Add(@([string]$r.PROVIDER, [string]$r.LOCATION, [string]$r.STATE, [math]::Round([double]$r.LAT, 4), [math]::Round([double]$r.LON, 4), [string]$r.D))
  }
  $cls = if ($code -eq 'W') { if ($r.WHEAT_CLASS) { [string]$r.WHEAT_CLASS } else { '' } } else { '' }
  $k = "$($locIndex[$lk])|$code|$cls"
  if (-not $byKey.ContainsKey($k)) { $byKey[$k] = New-Object System.Collections.ArrayList }
  [void]$byKey[$k].Add(@($ym, [string]$r.FUTURES_SYMBOL, [int]$r.BASIS_CENTS))
}
# keep the two nearest delivery months per location + grain (+ wheat class)
$bids = New-Object System.Collections.ArrayList
foreach ($k in $byKey.Keys) {
  $parts = $k.Split('|'); $seen = @{}
  foreach ($b in ($byKey[$k] | Sort-Object { $_[0] })) {
    if ($seen.ContainsKey($b[0])) { continue }; $seen[$b[0]] = 1
    $y = [math]::Floor(($b[0] - 1) / 12); $mo = $b[0] - $y * 12
    [void]$bids.Add(@([int]$parts[0], $parts[1], $parts[2], ('{0}-{1:00}' -f $y, $mo), $b[1], $b[2]))
    if ($seen.Count -ge 2) { break }
  }
}
$doc = [ordered]@{
  asOf = ($rows | ForEach-Object { [string]$_.D } | Sort-Object | Select-Object -Last 1)
  syncedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'); source = 'JSA.BASIS_TRACKER'
  # locs: [provider, location, state, lat, lon, bid date]; bids: [locIndex, grain C|S|W, wheat class, delivery YYYY-MM, futures symbol, basis cents]
  locs = $locs; bids = $bids
}
$out = Join-Path $outDir 'market-bids.json'
[System.IO.File]::WriteAllText($out, ($doc | ConvertTo-Json -Depth 4 -Compress), (New-Object System.Text.UTF8Encoding $false))
"asOf=$($doc.asOf) locations=$($locs.Count) bids=$($bids.Count) bytes=$((Get-Item $out).Length) file=$out"
