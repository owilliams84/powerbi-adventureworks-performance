# One build cycle: close Desktop, regenerate model and report, gate, reopen, refresh, cross-check,
# screenshot every page and crop. Desktop must be closed before generating - an open project
# writes its in-memory copy back over the files.
param([switch]$NoShots)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
Stop-Process -Name PBIDesktop -Force -ErrorAction SilentlyContinue; Start-Sleep 3
Stop-Process -Name msmdsrv -Force -ErrorAction SilentlyContinue; Start-Sleep 2
python etl\build_model.py; if ($LASTEXITCODE) { throw "build_model failed" }
powershell -NoProfile -ExecutionPolicy Bypass -File etl\check_tmdl.ps1 | Select-Object -First 1
python etl\build_report.py | Select-Object -Last 1; if ($LASTEXITCODE) { throw "build_report failed" }
$v = powerbi-report-author validate "AdventureWorks Performance.Report" | ConvertFrom-Json
"validate: $($v.data.result) errors=$($v.data.errorCount) warnings=$($v.data.warningCount)"
if ($v.data.errorCount -gt 0) { $v.data.diagnostics | ConvertTo-Json -Depth 6; throw "PBIR invalid" }
Start-Process -FilePath "$root\AdventureWorks Performance.pbip"; Start-Sleep -Seconds 40
$j = Start-Job { param($p) powershell -NoProfile -ExecutionPolicy Bypass -File "$p\etl\refresh_model.ps1" } -ArgumentList $root
Wait-Job $j -Timeout 420 | Out-Null; Receive-Job $j 2>&1 | Select-Object -Last 1
python etl\compare.py | Select-Object -Last 1
if ($NoShots) { return }
$pkg = (Get-AppxPackage -Name "*PowerBIDesktop*").InstallLocation; $env:PBI_DESKTOP_PATH = "$pkg\bin\PBIDesktop.exe"
powerbi-desktop screenshot pgSummary --output "$env:TEMP\aw_warm.png" --scale 2 | Out-Null; Start-Sleep 60
foreach ($p in "pgSummary", "pgProducts", "pgCustomers", "pgRegions") {
    powerbi-desktop screenshot $p --output "$env:TEMP\aw_raw_$p.png" --scale 2 | Out-Null; Start-Sleep 12
    Copy-Item "$env:TEMP\aw_raw_$p.png" "$root\screenshots\raw_$p.png" -Force
}
python etl\crop_screenshots.py
