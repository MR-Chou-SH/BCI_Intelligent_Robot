param(
    [ValidateRange(1, 6)]
    [int]$SessionNumber = 1,
    [switch]$Resume,
    [switch]$PhysicalTriggerPassed,
    [string]$ComPort = "COM11",
    [string]$Python = "C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe",
    [string]$DataRoot = "D:\EEG_Study\m37_prospective"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ArtifactRoot = Join-Path $RepoRoot "research_analysis\m37_acquisition_prep_20261005\attempt-01"
$FormalRoot = Join-Path $DataRoot "formal"
$SchedulePath = Join-Path $FormalRoot "schedule.json"
$SessionId = "session_{0:D3}" -f $SessionNumber
$OutputRoot = Join-Path $FormalRoot $SessionId

if (-not $PhysicalTriggerPassed) {
    Write-Output "BLOCKED: run the 3-5 trial real-trigger preflight and confirm it passes before formal acquisition."
    exit 2
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Output "BLOCKED: vendor Python not found: $Python"
    exit 2
}
if (-not (Test-Path -LiteralPath $SchedulePath -PathType Leaf)) {
    Write-Output "BLOCKED: frozen formal schedule is missing; run scripts\m37_preflight.ps1 first."
    exit 2
}
if ((Test-Path -LiteralPath $OutputRoot) -and -not $Resume) {
    Write-Output "BLOCKED: session root already exists. Use -Resume only for an intentionally paused resumable session."
    exit 2
}

$Arguments = @(
    "-B", "-m", "integration.m37_acquisition_prep", "serve",
    "--output-root", $OutputRoot, "--schedule", $SchedulePath,
    "--session-number", $SessionNumber, "--config", (Join-Path $ArtifactRoot "formal_config_template.json"),
    "--com", $ComPort, "--host", "0.0.0.0", "--port", "11001", "--quest-timeout", "30"
)
if ($Resume) { $Arguments += "--resume" }
Push-Location $RepoRoot
try {
    & $Python @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
