# Download get-pip.py with retries and fallbacks.
param(
    [Parameter(Mandatory = $true)]
    [string]$OutFile
)

$urls = @(
    "https://bootstrap.pypa.io/get-pip.py"
    "https://raw.githubusercontent.com/pypa/get-pip/main/public/get-pip.py"
)
$minBytes = 10000
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"

try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
}
catch {}

foreach ($url in $urls) {
    for ($i = 0; $i -lt 4; $i++) {
        try {
            Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
            Invoke-WebRequest -Uri $url -OutFile $OutFile -UseBasicParsing -UserAgent $ua -TimeoutSec 120
            if ((Test-Path $OutFile) -and ((Get-Item $OutFile).Length -ge $minBytes)) {
                Write-Host "[OK] get-pip.py saved"
                exit 0
            }
        }
        catch {
            Write-Host "[WARN] $url : $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 2
    }
    try {
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add("User-Agent", $ua)
        $wc.DownloadFile($url, $OutFile)
        $wc.Dispose()
        if ((Test-Path $OutFile) -and ((Get-Item $OutFile).Length -ge $minBytes)) {
            Write-Host "[OK] get-pip.py saved (WebClient)"
            exit 0
        }
    }
    catch { }
}

$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if ($curl) {
    foreach ($url in $urls) {
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
        & curl.exe -fsSL --max-time 120 -o $OutFile $url
        if (($LASTEXITCODE -eq 0) -and (Test-Path $OutFile) -and ((Get-Item $OutFile).Length -ge $minBytes)) {
            Write-Host "[OK] get-pip.py saved (curl)"
            exit 0
        }
    }
}

Write-Host "[ERROR] get-pip.py download failed"
exit 1
