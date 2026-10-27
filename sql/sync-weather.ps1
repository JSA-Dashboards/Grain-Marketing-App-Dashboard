# Pull National Weather Service forecasts for each farm location and write one JSON document per
# location (market/wx_<lat>_<lon>) for upload to the app.
# Usage:  .\sql\sync-weather.ps1
#   reads  sync\weather-locations.json   [{ "key": "wx_42.84_-93.62", "lat": 42.8429, "lon": -93.6231 }, ...]
#   writes sync\weather\<key>.json and prints one summary line per location
# Source: api.weather.gov (free, public; only the ZIP-center coordinates are sent).

$root = Split-Path $PSScriptRoot -Parent
$inFile = Join-Path $root 'sync\weather-locations.json'
$outDir = Join-Path $root 'sync\weather'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# If the scheduled task saved the farm list to sync\farmscan, build the locations file from it:
# only each farm's bidHome lat/lon is read; one location per rounded spot. The scan folder is then deleted.
$scan = Join-Path $root 'sync\farmscan'
if (Test-Path $scan) {
  $seen = [ordered]@{}
  foreach ($file in Get-ChildItem $scan -Recurse -Filter *.json) {
    try { $j = Get-Content $file.FullName -Raw | ConvertFrom-Json } catch { continue }
    $h = if ($j.data) { $j.data.bidHome } else { $j.bidHome }
    if (-not $h -or $null -eq $h.lat -or $null -eq $h.lon) { continue }
    $lat = [double]$h.lat; $lon = [double]$h.lon   # key matches the app's wx_<lat.toFixed(2)>_<lon.toFixed(2)>
    $key = 'wx_{0}_{1}' -f $lat.ToString('0.00', [Globalization.CultureInfo]::InvariantCulture), $lon.ToString('0.00', [Globalization.CultureInfo]::InvariantCulture)
    if (-not $seen.Contains($key)) { $seen[$key] = [ordered]@{ key = $key; lat = [double]$h.lat; lon = [double]$h.lon } }
  }
  [System.IO.File]::WriteAllText($inFile, (ConvertTo-Json @($seen.Values) -Compress), (New-Object System.Text.UTF8Encoding $false))
  Remove-Item -Recurse -Force $scan
  "locations=$($seen.Count) from farm scan"
}
if (-not (Test-Path $inFile)) { throw "No locations file at $inFile" }
$locs = Get-Content $inFile -Raw | ConvertFrom-Json
$headers = @{ 'User-Agent' = 'GrainMarketingDesk/1.0 (farm weather)'; 'Accept' = 'application/geo+json' }
function Get-Nws($url) { Invoke-RestMethod -Uri $url -Headers $headers -TimeoutSec 30 }
$mph = { param($s) if ($s -match '(\d+)\s*(to\s*(\d+))?\s*mph') { if ($matches[3]) { [int]$matches[3] } else { [int]$matches[1] } } else { $null } }

# Soil: Iowa State's Soil Moisture Network (Iowa Environmental Mesonet, public). Each location uses the nearest station
# reporting today within 75 miles: 4-inch soil temperature (now + 10 days of daily avg/low/high) and soil moisture at 12, 24 and 50 inches.
$soilStations = @(); $soilNow = @{}
try {
  $cur = Invoke-RestMethod -Uri 'https://mesonet.agron.iastate.edu/api/1/currents.json?network=ISUSM' -TimeoutSec 60
  foreach ($c in @($cur.data)) { $soilNow[$c.station] = $c }
  # the current-readings feed carries each reporting station's name and location: those are the stations to use
  # field stations only: skip vineyards, orchards and horticulture plots, and any 4-inch reading more than 12°F from the
  # statewide middle (a heated plot or a bad sensor, e.g. 83°F on an October night)
  $ok = @($cur.data | Where-Object { $null -ne $_.lat -and $null -ne $_.c1tmpf -and $_.name -notmatch 'Vineyard|Orchard|Horticulture' })
  $t4 = @($ok | ForEach-Object { [double]$_.c1tmpf } | Sort-Object); $mid = if ($t4.Count) { $t4[[int][math]::Floor($t4.Count / 2)] } else { 0 }
  $soilStations = @($ok | Where-Object { [math]::Abs([double]$_.c1tmpf - $mid) -le 12 } | ForEach-Object { [pscustomobject]@{ id = $_.station; name = $_.name; lon = [double]$_.lon; lat = [double]$_.lat } })
} catch { "soil stations unavailable: $($_.Exception.Message)" }
function Get-Miles($a1, $o1, $a2, $o2) {
  $r = [math]::PI / 180; $dl = ($a2 - $a1) * $r; $dn = ($o2 - $o1) * $r
  $h = [math]::Sin($dl / 2) * [math]::Sin($dl / 2) + [math]::Cos($a1 * $r) * [math]::Cos($a2 * $r) * [math]::Sin($dn / 2) * [math]::Sin($dn / 2)
  3958.8 * 2 * [math]::Atan2([math]::Sqrt($h), [math]::Sqrt(1 - $h)) }
function Get-Soil($lat, $lon) {
  if (-not $soilStations.Count) { return $null }
  $num = { param($v) if ($v -and $v -ne 'M') { [math]::Round([double]$v, 1) } else { $null } }
  $vwc = { param($v) $x = & $num $v; if ($null -ne $x -and $x -ge 5 -and $x -le 60) { $x } else { $null } }   # 1% or 70% is a sensor fault
  $near = { param($list) $list | ForEach-Object { [pscustomobject]@{ s = $_; mi = (Get-Miles $lat $lon $_.lat $_.lon) } } | Sort-Object mi | Select-Object -First 1 }
  $ts = & $near $soilStations
  if (-not $ts -or $ts.mi -gt 75) { return $null }
  # moisture can come from a different (nearest) station, since some stations only measure temperature
  $ms = & $near @($soilStations | Where-Object { $c = $soilNow[$_.id]; $c -and (& $vwc $c.c2smv) })
  $sts = (Get-Date).ToUniversalTime().AddDays(-11).ToString('yyyy-MM-ddT00:00Z'); $ets = (Get-Date).ToUniversalTime().AddDays(1).ToString('yyyy-MM-ddT00:00Z')
  $daily = { param($id) $csv = Invoke-RestMethod -TimeoutSec 60 -Uri ("https://mesonet.agron.iastate.edu/cgi-bin/request/isusm.py?mode=daily&station={0}&sts={1}&ets={2}&vars=soil04t,soil04tn,soil04tx,soil12vwc,soil24vwc,soil50vwc&format=comma&missing=M" -f $id, $sts, $ets)
    $rows = @{}; foreach ($line in ($csv -split "`n" | Select-Object -Skip 1)) { $f = $line.Trim() -split ','; if ($f.Count -ge 8) { $rows[$f[1]] = $f } }; $rows }
  $tr = & $daily $ts.s.id
  $mr = if ($ms -and $ms.mi -le 75) { if ($ms.s.id -eq $ts.s.id) { $tr } else { & $daily $ms.s.id } } else { @{} }
  $days = @($tr.Keys | Sort-Object | ForEach-Object { $f = $tr[$_]; $m = $mr[$_]
    [ordered]@{ d = $_; t4 = (& $num $f[2]); t4n = (& $num $f[3]); t4x = (& $num $f[4])
      v12 = $(if ($m) { & $vwc $m[5] } else { $null }); v24 = $(if ($m) { & $vwc $m[6] } else { $null }); v50 = $(if ($m) { & $vwc $m[7] } else { $null }) } })
  $c = $soilNow[$ts.s.id]; $cm = if ($ms -and $ms.mi -le 75) { $soilNow[$ms.s.id] } else { $null }
  $now = [ordered]@{ valid = $(if ($c) { $c.utc_valid } else { $null }); t4 = $(if ($c) { & $num $c.c1tmpf } else { $null }); t12 = $(if ($c) { & $num $c.c2tmpf } else { $null })
    v12 = $(if ($cm) { & $vwc $cm.c2smv } else { $null }); v24 = $(if ($cm) { & $vwc $cm.c3smv } else { $null }); v50 = $(if ($cm) { & $vwc $cm.c4smv } else { $null }) }
  [ordered]@{ station = $ts.s.id; name = $ts.s.name; miles = [math]::Round($ts.mi)
    mStation = $(if ($cm) { $ms.s.id } else { $null }); mName = $(if ($cm) { $ms.s.name } else { $null }); mMiles = $(if ($cm) { [math]::Round($ms.mi) } else { $null })
    source = 'Iowa State University Soil Moisture Network (Iowa Environmental Mesonet)'; now = $now; days = $days } }

foreach ($l in @($locs)) {
  try {
    $pt = Get-Nws ("https://api.weather.gov/points/{0},{1}" -f $l.lat, $l.lon)
    $p = $pt.properties
    $fc = Get-Nws $p.forecast
    $hr = Get-Nws $p.forecastHourly
    $grid = $null; try { $grid = Get-Nws $p.forecastGridData } catch { }
    $alerts = $null; try { $alerts = Get-Nws ("https://api.weather.gov/alerts/active?point={0},{1}" -f $l.lat, $l.lon) } catch { }

    # 12-hour periods for 7 days
    $periods = @($fc.properties.periods | ForEach-Object { [ordered]@{
      name = $_.name; start = $_.startTime; day = [bool]$_.isDaytime; temp = $_.temperature
      pop = if ($_.probabilityOfPrecipitation -and $null -ne $_.probabilityOfPrecipitation.value) { [int]$_.probabilityOfPrecipitation.value } else { 0 }
      wind = $_.windSpeed; windMax = (& $mph $_.windSpeed); dir = $_.windDirection; short = $_.shortForecast; detail = $_.detailedForecast } })
    # next 72 hours (covers "now" until the next morning's run, plus the bin-fan outlook); rh = relative humidity %, dew = dew point °F
    $hourly = @($hr.properties.periods | Select-Object -First 72 | ForEach-Object { [ordered]@{
      t = $_.startTime; temp = $_.temperature; short = $_.shortForecast; wind = (& $mph $_.windSpeed)
      pop = if ($_.probabilityOfPrecipitation -and $null -ne $_.probabilityOfPrecipitation.value) { [int]$_.probabilityOfPrecipitation.value } else { 0 }
      rh = if ($_.relativeHumidity -and $null -ne $_.relativeHumidity.value) { [int]$_.relativeHumidity.value } else { $null }
      dew = if ($_.dewpoint -and $null -ne $_.dewpoint.value) { [math]::Round([double]$_.dewpoint.value * 9 / 5 + 32) } else { $null } } })
    # expected rain over the next 7 days, from the grid's precipitation amounts (mm -> inches)
    $rain = $null
    if ($grid -and $grid.properties.quantitativePrecipitation) {
      $until = (Get-Date).ToUniversalTime().AddDays(7); $mm = 0
      foreach ($v in $grid.properties.quantitativePrecipitation.values) {
        $start = [datetime]::Parse(($v.validTime -split '/')[0]).ToUniversalTime()
        if ($start -lt $until -and $null -ne $v.value) { $mm += [double]$v.value } }
      $rain = [math]::Round($mm / 25.4, 2) }
    $al = @(); if ($alerts) { $al = @($alerts.features | ForEach-Object { [ordered]@{
      event = $_.properties.event; headline = $_.properties.headline; severity = $_.properties.severity; ends = $_.properties.ends } }) }
    $rl = $p.relativeLocation.properties
    $doc = [ordered]@{
      place = "$($rl.city), $($rl.state)"; lat = $l.lat; lon = $l.lon; office = $p.gridId
      asOf = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'); updated = $fc.properties.updated
      periods = $periods; hourly = $hourly; rain7 = $rain; alerts = $al; soil = $(try { Get-Soil ([double]$l.lat) ([double]$l.lon) } catch { $null }) }
    $out = Join-Path $outDir ("{0}.json" -f $l.key)
    [System.IO.File]::WriteAllText($out, ($doc | ConvertTo-Json -Depth 6 -Compress), (New-Object System.Text.UTF8Encoding $false))
    "$($l.key) ok place=$($doc.place) periods=$($periods.Count) rain7=$rain alerts=$($al.Count) soil=$(if ($doc.soil) { "$($doc.soil.station) $($doc.soil.miles)mi 4in=$($doc.soil.now.t4)" } else { 'none' }) bytes=$((Get-Item $out).Length)"
  } catch { "$($l.key) FAILED $($_.Exception.Message)" }
  Start-Sleep -Milliseconds 300   # be polite to the weather service
}
