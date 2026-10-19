# Evaluates every measure in Metrics for each fiscal year (and both channels) over one connection
# and prints errors and values (SVG measures shortened). A DAX error in a measure otherwise only
# shows as a grey box on the page.
param([string[]]$Years = @("FY2018", "FY2019", "FY2020"))
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$tmdl = Get-Content (Join-Path $root "AdventureWorks Performance.SemanticModel\definition\tables\Metrics.tmdl")
$names = $tmdl | Where-Object { $_ -match "^\tmeasure ('([^']+)'|(\S+)) =" } | ForEach-Object { if ($Matches[2]) { $Matches[2] } else { $Matches[3] } }
$msmdsrv = Get-CimInstance Win32_Process -Filter "Name='msmdsrv.exe'"
$port = (Get-NetTCPConnection -State Listen -OwningProcess $msmdsrv[0].ProcessId | Where-Object { $_.LocalAddress -eq "127.0.0.1" } | Select-Object -First 1).LocalPort
$pkg = (Get-AppxPackage -Name "*PowerBIDesktop*").InstallLocation
[void][Reflection.Assembly]::LoadFrom((Join-Path $pkg "bin\Microsoft.PowerBI.AdomdClient.dll"))
$probe = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port"); $probe.Open()
$c = $probe.CreateCommand(); $c.CommandText = "SELECT [CATALOG_NAME] FROM `$SYSTEM.DBSCHEMA_CATALOGS"; $r = $c.ExecuteReader(); [void]$r.Read(); $cat = $r.GetString(0); $r.Close(); $probe.Close()
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port;Initial Catalog=$cat"); $conn.Open()
$bad = 0
foreach ($fy in $Years) {
    Write-Output "== $fy"
    foreach ($n in $names) {
        $cmd = $conn.CreateCommand()
        $cmd.CommandText = "EVALUATE CALCULATETABLE(ROW(""v"", [$n]), TREATAS({""$fy""}, 'Date'[Fiscal Year]))"
        try {
            $r = $cmd.ExecuteReader(); [void]$r.Read(); $v = $r.GetValue(0); $r.Close()
            $s = if ($v -is [DBNull]) { "(blank)" } else { "$v" }
            if ($s.StartsWith("data:image")) { $s = "svg " + $s.Length + " chars" }
            if ($s.Length -gt 150) { $s = $s.Substring(0, 150) + "..." }
            Write-Output ("  {0,-32} {1}" -f $n, $s)
        } catch {
            $e = $_.Exception; while ($e.InnerException) { $e = $e.InnerException }
            Write-Output ("  {0,-32} ERROR {1}" -f $n, $e.Message.Split("`n")[0]); $bad++
        }
    }
}
$conn.Close()
Write-Output "errors: $bad"
