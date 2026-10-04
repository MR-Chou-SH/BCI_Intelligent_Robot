param(
    [string]$ComPort = "COM11",
    [string]$QuestIp = "",
    [string]$Python = "C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe",
    [string]$DataRoot = "D:\EEG_Study\m37_prospective"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ArtifactRoot = Join-Path $RepoRoot "research_analysis\m37_acquisition_prep_20261005\attempt-01"
$Stamp = Get-Date -Format "yyyyMMddTHHmmss"
$AttemptRoot = Join-Path (Join-Path $DataRoot "_preflight") ("attempt_" + $Stamp)
$FormalRoot = Join-Path $DataRoot "formal"
$SchedulePath = Join-Path $FormalRoot "schedule.json"
$OutputRoot = Join-Path (Join-Path $FormalRoot "preflight_placeholder") ("session_" + $Stamp)

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Output "BLOCKERS: vendor Python not found: $Python"
    exit 2
}
if (-not (Test-Path -LiteralPath $ArtifactRoot -PathType Container)) {
    Write-Output "BLOCKERS: M37 config template missing: $ArtifactRoot"
    exit 2
}
$null = New-Item -ItemType Directory -Path $AttemptRoot -Force
$null = New-Item -ItemType Directory -Path (Split-Path -Parent $OutputRoot) -Force
$null = New-Item -ItemType Directory -Path $FormalRoot -Force
Push-Location -LiteralPath $RepoRoot
if (-not (Test-Path -LiteralPath $SchedulePath -PathType Leaf)) {
    & $Python -B -m integration.m37_acquisition_prep schedule --output $SchedulePath --sessions 6 --trials-per-session 63 --seed 37005
    if ($LASTEXITCODE -ne 0) { throw "Could not freeze the M37 formal schedule." }
}

$PreflightArgs = @(
    "-B", "-m", "integration.m37_acquisition_prep", "preflight",
    "--config", (Join-Path $ArtifactRoot "formal_config_template.json"),
    "--output-root", $OutputRoot, "--com", $ComPort,
    "--host", "0.0.0.0", "--port", "11001",
    "--report", (Join-Path $AttemptRoot "preflight_summary.json")
)
if ($QuestIp) { $PreflightArgs += @("--quest-ip", $QuestIp) }
& $Python @PreflightArgs | Out-Host
$StaticStatus = $LASTEXITCODE

& $Python -B -m integration.m37_acquisition_prep probe-quest --report (Join-Path $AttemptRoot "quest_transport.json") --host 0.0.0.0 --port 11001 --timeout 20 | Out-Host
$QuestStatus = $LASTEXITCODE

$Nd8Root = Join-Path $AttemptRoot "nd8_probe"
& $Python -B -m integration.m37_acquisition_prep probe-nd8 --output-root $Nd8Root --com $ComPort --duration 3 --open-timeout 20 | Out-Host
$Nd8Status = $LASTEXITCODE

$SummaryPath = Join-Path $AttemptRoot "preflight_summary.json"
$Summary = Get-Content -LiteralPath $SummaryPath -Raw | ConvertFrom-Json
$QuestProbe = Get-Content -LiteralPath (Join-Path $AttemptRoot "quest_transport.json") -Raw | ConvertFrom-Json
$Nd8Probe = Get-Content -LiteralPath (Join-Path $Nd8Root "probe_report.json") -Raw | ConvertFrom-Json
$Summary.checks | Add-Member -NotePropertyName questTcpReadyEventReceived -NotePropertyValue ($QuestProbe.status -eq "PASS") -Force
$Summary.checks | Add-Member -NotePropertyName nd8PortOpenedAndStreamStarted -NotePropertyValue ($Nd8Probe.status -eq "PASS") -Force
$Summary.hardwareBoundary.questConnected = ($QuestProbe.status -eq "PASS")
$Summary.hardwareBoundary.nd8PortOpened = ($Nd8Probe.packetCount -gt 0)
$Summary.hardwareBoundary.nd8StreamStarted = ($Nd8Probe.status -eq "PASS")
$Summary.blockers = @()
if ($StaticStatus -ne 0) { $Summary.blockers += "static preflight failed" }
if ($QuestStatus -ne 0) { $Summary.blockers += "Quest runtime did not connect and publish m19_research_ready" }
if ($Nd8Status -ne 0) { $Summary.blockers += "ND8 live stream probe failed" }
if ($Summary.blockers.Count -eq 0) {
    $Summary.status = "READY FOR FORMAL ACQUISITION"
}
else {
    $Summary.status = "BLOCKED"
}
$Summary | ConvertTo-Json -Depth 24 | Set-Content -LiteralPath $SummaryPath -Encoding UTF8
Pop-Location

if ($StaticStatus -eq 0 -and $QuestStatus -eq 0 -and $Nd8Status -eq 0) {
    Write-Output "READY FOR FORMAL ACQUISITION"
    exit 0
}

$Blockers = @()
if ($StaticStatus -ne 0) { $Blockers += "static preflight failed" }
if ($QuestStatus -ne 0) { $Blockers += "Quest runtime did not connect and publish m19_research_ready" }
if ($Nd8Status -ne 0) { $Blockers += "ND8 live stream probe failed" }
Write-Output ("BLOCKERS: " + ($Blockers -join "; "))
exit 2
