param(
    [string]$Layout = 'kitchen_v3_norice_candidate_005',
    [Nullable[int64]]$Seed = $null,
    [ValidateSet('fixed','research')][string]$InitializationProfile = 'fixed',
    [ValidateSet('manual','realtime')][string]$Mode = 'manual',
    [Parameter(Mandatory=$true)][string]$DataRoot,
    [string]$ReadyFile = '',
    [int]$PlannerPort = 11031,
    [int]$WorldPort = 11032,
    [string]$BindHost = '127.0.0.1',
    [double]$RunSeconds = 0,
    [string]$PythonExe = 'D:/VLA_Kitchen_Data/.envs/kitchen-sim/Scripts/python.exe'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
function Resolve-WorktreePath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) {
        if ($Value -notmatch '^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') {
            throw 'Use a fully qualified absolute path or a worktree-relative path; drive/root-relative paths are ambiguous.'
        }
        return [IO.Path]::GetFullPath($Value)
    }
    # Anchor to the script's worktree, not the caller's current directory (also on PS 5.1).
    return [IO.Path]::GetFullPath((Join-Path $root $Value))
}
$output = Resolve-WorktreePath $DataRoot
$allowed = [IO.Path]::GetFullPath((Join-Path $root 'experiments/kitchen_platform_v3')) + [IO.Path]::DirectorySeparatorChar
if (-not $output.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'DataRoot must be inside this worktree experiments/kitchen_platform_v3; old data roots are prohibited.'
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Existing kitchen Python missing: $PythonExe" }
if ([string]::IsNullOrWhiteSpace($ReadyFile)) { $ReadyFile = Join-Path $output ('ready_' + [Guid]::NewGuid().ToString('N') + '.json') }
$ready = Resolve-WorktreePath $ReadyFile
if (-not $ready.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)) { throw 'ReadyFile must stay in the new run tree.' }
Push-Location -LiteralPath $root
try {
    $seedArguments = @()
    if ($null -ne $Seed) { $seedArguments = @('--seed', [string]$Seed) }
    & $PythonExe -B -m integration.kitchen.platform_server --layout $Layout @seedArguments --mode $Mode `
        --initialization-profile $InitializationProfile `
        --data-root $output --ready-file $ready --planner-port $PlannerPort --world-port $WorldPort `
        --bind-host $BindHost --run-seconds $RunSeconds
    exit $LASTEXITCODE
}
finally { Pop-Location }
