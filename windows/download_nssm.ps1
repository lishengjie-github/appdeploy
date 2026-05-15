# Download NSSM win64 nssm.exe into the given directory (default: script directory).
# Tries nssm.cc first, then GitHub mirror. ASCII only.
param(
    [string]$TargetDir = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

$sources = @(
    @{ Url = "https://nssm.cc/release/nssm-2.24.zip"; Note = "nssm.cc official layout nssm-2.24\win64\" }
    @{ Url = "https://github.com/fawno/nssm.cc/releases/download/v2.24.1/nssm-v2.24.1-Win64.zip"; Note = "GitHub Win64 bundle" }
)

function Copy-NssmFromZip {
    param([string]$ZipPath, [string]$ExtractRoot)
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractRoot -Force
    $win64 = Get-ChildItem -Path $ExtractRoot -Recurse -Filter "nssm.exe" |
        Where-Object { $_.FullName -match '[\\/]win64[\\/]' } |
        Select-Object -First 1
    if (-not $win64) {
        $win64 = Get-ChildItem -Path $ExtractRoot -Recurse -Filter "nssm.exe" | Select-Object -First 1
    }
    if (-not $win64) { throw "nssm.exe not found inside zip" }
    $dest = Join-Path $TargetDir "nssm.exe"
    Copy-Item -LiteralPath $win64.FullName -Destination $dest -Force
    Write-Host "[OK] nssm.exe -> $dest"
}

$zip = Join-Path $env:TEMP ("nssm-" + [guid]::NewGuid().ToString("n") + ".zip")
$extract = Join-Path $env:TEMP ("nssm-extract-" + [guid]::NewGuid().ToString("n"))

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $lastErr = $null
    foreach ($src in $sources) {
        try {
            Write-Host "[..] Trying: $($src.Url)"
            Invoke-WebRequest -Uri $src.Url -OutFile $zip -UseBasicParsing
            New-Item -ItemType Directory -Path $extract -Force | Out-Null
            Copy-NssmFromZip -ZipPath $zip -ExtractRoot $extract
            exit 0
        }
        catch {
            $lastErr = $_.Exception.Message
            Write-Host "[WARN] $($src.Url) -> $lastErr"
            Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
            $extract = Join-Path $env:TEMP ("nssm-extract-" + [guid]::NewGuid().ToString("n"))
            New-Item -ItemType Directory -Path $extract -Force -ErrorAction SilentlyContinue | Out-Null
        }
    }
    Write-Host "[ERROR] All download sources failed. Last: $lastErr"
    exit 1
}
finally {
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
}
