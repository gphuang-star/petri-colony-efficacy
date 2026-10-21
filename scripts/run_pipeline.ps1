[CmdletBinding()]
param(
    [string]$MeasurementsCsv = "",
    [string]$PlateManifestCsv = "",
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [double]$PlugDiameterMm = 0,
    [string]$CkNames = "CK,空白对照,对照,Control",
    [double]$DishDiameterMm = 86,
    [int]$ExpectedMeasurements = 2,
    [int]$ExpectedTotalMeasurements = 6,
    [int]$MinimumMeasurements = 1,
    [int]$ExpectedReplicates = 3,
    [double]$CvThreshold = 0.15,
    [double]$IrregularCvThreshold = 0.25,
    [double]$EcdReviewThreshold = 0.15,
    [ValidateSet("plug_corrected", "total_diameter")][string]$FormulaMode = "plug_corrected",
    [string]$PythonExe = "",
    [switch]$FailOnReview
)

$ErrorActionPreference = "Stop"
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
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        } catch { }
    }
    throw "找不到可用的 Python。请设置 COLONY_PYTHON 或用 -PythonExe 指定。"
}

$resolved = Resolve-PythonCommand -Explicit $PythonExe
$pyExe = $resolved.Exe
$pyPrefix = $resolved.Prefix

$computeScript = Join-Path $scriptDir "compute_efficacy.py"
$measureScript = Join-Path $scriptDir "measure_colony_image.py"
if (-not (Test-Path -LiteralPath $computeScript)) { throw "缺少脚本: $computeScript" }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$outputFull = (Resolve-Path -LiteralPath $OutputDir).Path

$inputCsv = ""
if ($MeasurementsCsv) {
    if (-not (Test-Path -LiteralPath $MeasurementsCsv)) { throw "测量 CSV 不存在: $MeasurementsCsv" }
    $inputCsv = (Resolve-Path -LiteralPath $MeasurementsCsv).Path
    Write-Host "模式: CSV-only"
} elseif ($PlateManifestCsv) {
    if (-not (Test-Path -LiteralPath $PlateManifestCsv)) { throw "图片清单不存在: $PlateManifestCsv" }
    if (-not (Test-Path -LiteralPath $measureScript)) { throw "缺少脚本: $measureScript" }
    $manifestFull = (Resolve-Path -LiteralPath $PlateManifestCsv).Path
    $measureDir = Join-Path $outputFull "auto_measurements"
    New-Item -ItemType Directory -Force -Path $measureDir | Out-Null
    Write-Host "模式: 图像辅助测量（实验性）"
    $measureArgs = @(
        $measureScript, "--manifest", $manifestFull, "--output-dir", $measureDir,
        "--dish-diameter-mm", $DishDiameterMm,
        "--cv-threshold", $CvThreshold,
        "--irregular-cv-threshold", $IrregularCvThreshold,
        "--ecd-review-threshold", $EcdReviewThreshold
    )
    & $pyExe @pyPrefix @measureArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $inputCsv = Join-Path $measureDir "measurements_auto.csv"
    if (-not (Test-Path -LiteralPath $inputCsv)) { throw "图像测量没有生成 measurements_auto.csv" }
} else {
    throw "必须指定 -MeasurementsCsv 或 -PlateManifestCsv"
}

$computeArgs = @(
    $computeScript, "--input", $inputCsv, "--output-dir", $outputFull,
    "--dish-diameter-mm", $DishDiameterMm,
    "--ck-names", $CkNames,
    "--expected-measurements", $ExpectedMeasurements,
    "--expected-total-measurements", $ExpectedTotalMeasurements,
    "--min-measurements", $MinimumMeasurements,
    "--expected-replicates", $ExpectedReplicates,
    "--cv-threshold", $CvThreshold,
    "--irregular-cv-threshold", $IrregularCvThreshold,
    "--ecd-review-threshold", $EcdReviewThreshold,
    "--formula-mode", $FormulaMode
)
if ($PlugDiameterMm -gt 0) {
    $computeArgs += @("--plug-diameter-mm", $PlugDiameterMm)
}
if ($FailOnReview) {
    $computeArgs += "--fail-on-review"
}

& $pyExe @pyPrefix @computeArgs
exit $LASTEXITCODE
