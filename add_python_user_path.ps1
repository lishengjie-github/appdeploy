# Append bundled Python (and Scripts if present) to the *User* PATH. ASCII only.
# Usage: powershell -File add_python_user_path.ps1 "C:\path\to\folder\containing\python.exe"
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonHome
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonHome)) {
    Write-Host "[ERROR] Path not found: $PythonHome"
    exit 1
}

$PythonHome = (Resolve-Path -LiteralPath $PythonHome).Path.TrimEnd('\')
$pyExe = Join-Path $PythonHome "python.exe"
if (-not (Test-Path -LiteralPath $pyExe)) {
    Write-Host "[ERROR] python.exe not found under: $PythonHome"
    exit 1
}

$dirs = New-Object System.Collections.ArrayList
[void]$dirs.Add($PythonHome)

$scripts = Join-Path $PythonHome "Scripts"
if (Test-Path -LiteralPath $scripts) {
    [void]$dirs.Add($scripts)
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -eq $userPath) { $userPath = "" }

$existing = @(
    $userPath -split ';' |
    Where-Object { $_ -and $_.Trim() -ne '' } |
    ForEach-Object { $_.Trim().TrimEnd('\') }
)

$merged = New-Object System.Collections.ArrayList
foreach ($p in $existing) {
    try {
        $full = (Resolve-Path -LiteralPath $p -ErrorAction Stop).Path.TrimEnd('\')
        [void]$merged.Add($full)
    }
    catch {
        [void]$merged.Add($p)
    }
}

foreach ($d in $dirs) {
    $norm = $d.TrimEnd('\')
    $have = $false
    foreach ($m in $merged) {
        if ([string]::Equals($m, $norm, [StringComparison]::OrdinalIgnoreCase)) {
            $have = $true
            break
        }
    }
    if (-not $have) {
        [void]$merged.Add($norm)
    }
}

$newPath = ($merged | Where-Object { $_ }) -join ';'
[Environment]::SetEnvironmentVariable("Path", $newPath, "User")

Write-Host "[OK] User PATH updated; python.exe dir: $PythonHome"
Write-Host "[INFO] Open a *new* Command Prompt for ""python"" / ""pip"" to work."
exit 0
