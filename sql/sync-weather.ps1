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
if (-not (Test-Path $inFile)) { throw "No locations file at $inFile" }
$locs = Get-Content $inFile -Raw | ConvertFrom-Json
$headers = @{ 'User-Agent' = 'GrainMarketingDesk/1.0 (farm weather)'; 'Accept' = 'application/geo+json' }
function Get-Nws($url) { Invoke-RestMethod -Uri $url -Headers $headers -TimeoutSec 30 }
$mph = { param($s) if ($s -match '(\d+)\s*(to\s*(\d+))?\s*mph') { if ($matches[3]) { [int]$matches[3] } else { [int]$matches[1] } } else { $null } }

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
    # next 24 hours
    $hourly = @($hr.properties.periods | Select-Object -First 24 | ForEach-Object { [ordered]@{
      t = $_.startTime; temp = $_.temperature; short = $_.shortForecast; wind = (& $mph $_.windSpeed)
      pop = if ($_.probabilityOfPrecipitation -and $null -ne $_.probabilityOfPrecipitation.value) { [int]$_.probabilityOfPrecipitation.value } else { 0 } } })
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
      periods = $periods; hourly = $hourly; rain7 = $rain; alerts = $al }
    $out = Join-Path $outDir ("{0}.json" -f $l.key)
    [System.IO.File]::WriteAllText($out, ($doc | ConvertTo-Json -Depth 5 -Compress), (New-Object System.Text.UTF8Encoding $false))
    "$($l.key) ok place=$($doc.place) periods=$($periods.Count) rain7=$rain alerts=$($al.Count) bytes=$((Get-Item $out).Length)"
  } catch { "$($l.key) FAILED $($_.Exception.Message)" }
  Start-Sleep -Milliseconds 300   # be polite to the weather service
}
