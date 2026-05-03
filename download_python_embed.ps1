# Download Windows amd64 embeddable Python zip. Retries, mirrors, WebClient, BitsTransfer, curl.
# Usage: powershell -File download_python_embed.ps1 -Version 3.10.11 -OutFile C:\path\python-embed.zip
param(
    [string]$Version = "3.10.11",
    [Parameter(Mandatory = $true)]
    [string]$OutFile
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$zipName = "python-$Version-embed-amd64.zip"
# Order: official first, then common CN mirrors (same file content as python.org)
$baseUrls = @(
    "https://www.python.org/ftp/python/$Version/$zipName"
    "https://mirrors.aliyun.com/python-release/windows/$Version/$zipName"
    "https://mirrors.tuna.tsinghua.edu.cn/python/$Version/$zipName"
)
$minBytes = 800000

function Test-ZipFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $len = (Get-Item -LiteralPath $Path).Length
    return ($len -ge $minBytes)
}

try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor
        [Net.SecurityProtocolType]::Tls11 -bor
        [Net.SecurityProtocolType]::Tls
}
catch {}

$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

function Try-Iwr {
    param([string]$Url)
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -UserAgent $ua -TimeoutSec 180
        return (Test-ZipFile $OutFile)
    }
    catch {
        Write-Host "[WARN] IWR $Url : $($_.Exception.Message)"
        return $false
    }
}

function Try-WebClient {
    param([string]$Url)
    try {
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add("User-Agent", $ua)
        $wc.DownloadFile($Url, $OutFile)
        $wc.Dispose()
        return (Test-ZipFile $OutFile)
    }
    catch {
        Write-Host "[WARN] WebClient $Url : $($_.Exception.Message)"
        return $false
    }
}

function Try-Bits {
    param([string]$Url)
    try {
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
        Import-Module BitsTransfer -ErrorAction Stop
        Start-BitsTransfer -Source $Url -Destination $OutFile -TransferPolicy Always -ErrorAction Stop
        return (Test-ZipFile $OutFile)
    }
    catch {
        Write-Host "[WARN] BitsTransfer $Url : $($_.Exception.Message)"
        return $false
    }
}

function Try-CurlExe {
    param([string]$Url)
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) { return $false }
    try {
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
        & curl.exe -fsSL --connect-timeout 30 --max-time 300 -A $ua -o $OutFile $Url
        if ($LASTEXITCODE -ne 0) { return $false }
        return (Test-ZipFile $OutFile)
    }
    catch {
        return $false
    }
}

foreach ($url in $baseUrls) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Write-Host "[..] $url (attempt $attempt)"
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue

        if (Try-Iwr $url) { Write-Host "[OK] Saved $OutFile"; exit 0 }
        Start-Sleep -Seconds 2

        if (Try-WebClient $url) { Write-Host "[OK] Saved $OutFile (WebClient)"; exit 0 }

        if (Try-Bits $url) { Write-Host "[OK] Saved $OutFile (BitsTransfer)"; exit 0 }

        if (Try-CurlExe $url) { Write-Host "[OK] Saved $OutFile (curl)"; exit 0 }

        Start-Sleep -Seconds 3
    }
}

Write-Host "[ERROR] All download methods failed. Place file manually as:"
Write-Host "        $zipName -> copy to script folder as python-embed.zip"
exit 1
