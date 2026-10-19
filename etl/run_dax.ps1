# Runs a DAX query file against the model open in Power BI Desktop and writes the result as JSON.
#   powershell -ExecutionPolicy Bypass -File etl/run_dax.ps1 -Query q.dax -Out result.json
param([Parameter(Mandatory)][string]$Query, [Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = "Stop"
$msmdsrv = Get-CimInstance Win32_Process -Filter "Name='msmdsrv.exe'"
if (-not $msmdsrv) { throw "msmdsrv.exe is not running - open the PBIP in Desktop first." }
$port = $null
foreach ($proc in $msmdsrv) {
    $conn = Get-NetTCPConnection -State Listen -OwningProcess $proc.ProcessId -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalAddress -eq "127.0.0.1" } | Select-Object -First 1
    if ($conn) { $port = $conn.LocalPort; break }
}
$pkg = (Get-AppxPackage -Name "*PowerBIDesktop*").InstallLocation
$adomd = @("Microsoft.PowerBI.AdomdClient.dll", "Microsoft.AnalysisServices.AdomdClient.dll") |
         ForEach-Object { Join-Path $pkg "bin\$_" } | Where-Object { Test-Path $_ } | Select-Object -First 1
[void][Reflection.Assembly]::LoadFrom($adomd)
$probe = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port")
$probe.Open(); $c = $probe.CreateCommand(); $c.CommandText = "SELECT [CATALOG_NAME] FROM `$SYSTEM.DBSCHEMA_CATALOGS"
$r = $c.ExecuteReader(); [void]$r.Read(); $catalog = $r.GetString(0); $r.Close(); $probe.Close()
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port;Initial Catalog=$catalog")
$conn.Open()
$cmd = $conn.CreateCommand(); $cmd.CommandTimeout = 300
$cmd.CommandText = [IO.File]::ReadAllText((Resolve-Path $Query))
$rows = @()
try {
    $r = $cmd.ExecuteReader()
    do {
        while ($r.Read()) {
            $row = [ordered]@{}
            for ($i = 0; $i -lt $r.FieldCount; $i++) { $v = $r.GetValue($i); $row[$r.GetName($i)] = if ($v -is [DBNull]) { $null } else { $v } }
            $rows += [pscustomobject]$row
        }
    } while ($r.NextResult())
    $r.Close()
    $json = ConvertTo-Json -InputObject @($rows) -Depth 4 -Compress
} catch {
    $e = $_.Exception; while ($e.InnerException) { $e = $e.InnerException }
    $json = ConvertTo-Json @{ error = $e.Message } -Compress
}
[IO.File]::WriteAllText($Out, $json, (New-Object Text.UTF8Encoding $false))
$conn.Close()
Write-Output "wrote $Out"
