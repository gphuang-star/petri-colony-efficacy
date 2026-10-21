[CmdletBinding()]
param(
    [string]$PythonExe = ""
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-PythonCommand {
    param([string]$Explicit)
    $candidates = @()
    if ($Explicit) { $candidates += @{ Exe = $Explicit; Prefix = @() } }
    if ($env:COLONY_PYTHON) { $candidates += @{ Exe = $env:COLONY_PYTHON; Prefix = @() } }
    $candidates += @{ Exe = "py"; Prefix = @("-3") }
    $candidates += @{ Exe = "python"; Prefix = @() }
    foreach ($candidate in $candidates) {
        if (-not $candidate.Exe) { continue }
        try {
            $prefix = $candidate.Prefix
            & $candidate.Exe @prefix -c "import sys" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    return $null
}

$resolved = Resolve-PythonCommand -Explicit $PythonExe
if (-not $resolved) {
    Write-Host "Python: 未找到（仅 CSV 模式也可由 PowerShell 查看结果，但推荐安装 Python）"
    exit 1
}
$pyExe = $resolved.Exe
$pyPrefix = $resolved.Prefix
Write-Host "Python: $pyExe $($pyPrefix -join ' ')"
& $pyExe @pyPrefix --version
& $pyExe @pyPrefix -c "import importlib.util as u; mods=['numpy','cv2','PIL','pandas','skimage']; [print(m, 'OK' if u.find_spec(m) else 'MISSING') for m in mods]"

foreach ($name in @("compute_efficacy.py", "measure_colony_image.py")) {
    $path = Join-Path $scriptDir $name
    if (Test-Path -LiteralPath $path) {
        & $pyExe @pyPrefix -m py_compile $path
        if ($LASTEXITCODE -eq 0) { Write-Host "syntax $name OK" } else { Write-Host "syntax $name FAILED" }
    } else {
        Write-Host "missing $path"
    }
}
