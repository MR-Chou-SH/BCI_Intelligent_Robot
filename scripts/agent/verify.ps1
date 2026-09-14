[CmdletBinding()]
param(
    [string]$PythonExecutable = 'C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe',
    [string]$OutputDirectory,
    [ValidateRange(1, 3600)]
    [int]$TimeoutSeconds = 300
)

$ErrorActionPreference = 'Stop'
$script:Checks = @()
$script:Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function ConvertTo-WindowsArgument {
    param([string]$Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]'\') {
            $backslashes++
            continue
        }
        if ($character -eq [char]'"') {
            [void]$builder.Append(('\' * (2 * $backslashes + 1)))
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * (2 * $backslashes)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Format-Command {
    param([string]$FilePath, [string[]]$Arguments)

    $formattedArguments = @($Arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' '
    if ([string]::IsNullOrWhiteSpace($FilePath)) {
        return "<unavailable> $formattedArguments".Trim()
    }
    return ('& "{0}" {1}' -f $FilePath, $formattedArguments).Trim()
}

function Get-ShortSummary {
    param([int]$ExitCode, [string]$Stdout, [string]$Stderr)

    $lines = @((($Stdout + "`n" + $Stderr) -split "`r?`n") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($ExitCode -eq 0) {
        $ranLine = $lines | Where-Object { $_ -match '^Ran \d+ tests? in ' } | Select-Object -Last 1
        if ($ranLine) { return "$ranLine; OK" }
        return 'Process exited successfully.'
    }

    $lastLine = $lines | Select-Object -Last 1
    if (-not $lastLine) { return "Process exited with code $ExitCode." }
    $lastLine = $lastLine.Trim()
    if ($lastLine.Length -gt 240) { $lastLine = $lastLine.Substring(0, 237) + '...' }
    return "Exit code $ExitCode; $lastLine"
}

function Add-CheckRecord {
    param([pscustomobject]$Record)
    $script:Checks += $Record
    return $Record
}

function Add-BlockedCheck {
    param(
        [string]$Id,
        [string]$Name,
        [string]$Command,
        [string]$Summary,
        [string]$RunDirectory
    )

    $started = [DateTime]::UtcNow
    $stdoutPath = Join-Path $RunDirectory "$Id.stdout.log"
    $stderrPath = Join-Path $RunDirectory "$Id.stderr.log"
    [System.IO.File]::WriteAllText($stdoutPath, '', $script:Utf8NoBom)
    [System.IO.File]::WriteAllText($stderrPath, '', $script:Utf8NoBom)
    return Add-CheckRecord ([pscustomobject]@{
        id = $Id
        name = $Name
        command = $Command
        startedAtUtc = $started.ToString('o')
        completedAtUtc = [DateTime]::UtcNow.ToString('o')
        durationMilliseconds = 0
        exitCode = $null
        status = 'BLOCKED'
        summary = $Summary
        stdoutLogPath = $stdoutPath
        stderrLogPath = $stderrPath
    })
}

function Invoke-NativeCheck {
    param(
        [string]$Id,
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$RunDirectory,
        [int]$Timeout = 300,
        [ValidateSet('FAIL', 'BLOCKED')]
        [string]$FailureStatus = 'FAIL'
    )

    $command = Format-Command $FilePath $Arguments
    $started = [DateTime]::UtcNow
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $stdout = ''
    $stderr = ''
    $exitCode = $null
    $status = 'BLOCKED'
    $summary = ''
    $stdoutPath = Join-Path $RunDirectory "$Id.stdout.log"
    $stderrPath = Join-Path $RunDirectory "$Id.stderr.log"

    if ([string]::IsNullOrWhiteSpace($FilePath) -or
        ([System.IO.Path]::IsPathRooted($FilePath) -and -not (Test-Path -LiteralPath $FilePath -PathType Leaf))) {
        $summary = "Executable is unavailable: $FilePath"
    }
    else {
        $process = $null
        try {
            $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
            $startInfo.FileName = $FilePath
            $startInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' ')
            $startInfo.WorkingDirectory = $script:RepoRoot
            $startInfo.UseShellExecute = $false
            $startInfo.CreateNoWindow = $true
            $startInfo.RedirectStandardOutput = $true
            $startInfo.RedirectStandardError = $true
            $startInfo.StandardOutputEncoding = $script:Utf8NoBom
            $startInfo.StandardErrorEncoding = $script:Utf8NoBom

            $process = [System.Diagnostics.Process]::new()
            $process.StartInfo = $startInfo
            if (-not $process.Start()) { throw 'Process.Start returned false.' }
            $stdoutTask = $process.StandardOutput.ReadToEndAsync()
            $stderrTask = $process.StandardError.ReadToEndAsync()

            if (-not $process.WaitForExit($Timeout * 1000)) {
                $process.Kill()
                $process.WaitForExit()
                $stdout = $stdoutTask.GetAwaiter().GetResult()
                $stderr = $stderrTask.GetAwaiter().GetResult()
                $exitCode = -1
                $status = 'FAIL'
                $summary = "Timed out after $Timeout seconds; process was stopped."
            }
            else {
                $process.WaitForExit()
                $stdout = $stdoutTask.GetAwaiter().GetResult()
                $stderr = $stderrTask.GetAwaiter().GetResult()
                $exitCode = $process.ExitCode
                $status = if ($exitCode -eq 0) { 'PASS' } else { $FailureStatus }
                $summary = Get-ShortSummary $exitCode $stdout $stderr
            }
        }
        catch {
            $stderr = $_.Exception.Message
            $summary = "Could not run check: $($_.Exception.Message)"
            $status = 'BLOCKED'
        }
        finally {
            if ($process) { $process.Dispose() }
        }
    }

    [System.IO.File]::WriteAllText($stdoutPath, $stdout, $script:Utf8NoBom)
    [System.IO.File]::WriteAllText($stderrPath, $stderr, $script:Utf8NoBom)
    $timer.Stop()
    return Add-CheckRecord ([pscustomobject]@{
        id = $Id
        name = $Name
        command = $command
        startedAtUtc = $started.ToString('o')
        completedAtUtc = [DateTime]::UtcNow.ToString('o')
        durationMilliseconds = $timer.ElapsedMilliseconds
        exitCode = $exitCode
        status = $status
        summary = $summary
        stdoutLogPath = $stdoutPath
        stderrLogPath = $stderrPath
    })
}

$script:RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path ([System.IO.Path]::GetTempPath()) 'BCI_Intelligent_Robot\verification'
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $script:RepoRoot $OutputDirectory
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
[void][System.IO.Directory]::CreateDirectory($OutputDirectory)

$runId = 'verify-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + "-$PID"
$runDirectory = Join-Path $OutputDirectory $runId
$suffix = 1
while (Test-Path -LiteralPath $runDirectory) {
    $runDirectory = Join-Path $OutputDirectory "$runId-$suffix"
    $suffix++
}
[void][System.IO.Directory]::CreateDirectory($runDirectory)
$startedAtUtc = [DateTime]::UtcNow.ToString('o')

# The live-nd8 test module was reviewed: device startup is injected/mocked, and its
# COM11 CLI cases use --dry-run. Never replace these unittest calls with live CLI modes.
$pythonCode = 'import json,platform,sys,numpy;print(json.dumps({"implementation":platform.python_implementation(),"version":".".join(map(str,sys.version_info[:3])),"architecture":platform.architecture()[0],"numpy":numpy.__version__}))'
$runtimeCommand = Format-Command $PythonExecutable @('-B', '-c', $pythonCode)
$runtimeCheck = Invoke-NativeCheck -Id 'python-runtime' -Name 'Python runtime and required dependency' `
    -FilePath $PythonExecutable -Arguments @('-B', '-c', $pythonCode) -RunDirectory $runDirectory `
    -Timeout $TimeoutSeconds -FailureStatus 'BLOCKED'

$pythonInfo = $null
$pythonReady = $false
if ($runtimeCheck.Status -eq 'PASS') {
    try {
        $runtimeOutput = [System.IO.File]::ReadAllText($runtimeCheck.stdoutLogPath)
        $pythonInfo = $runtimeOutput | ConvertFrom-Json -ErrorAction Stop
        $pythonReady = ($pythonInfo.implementation -eq 'CPython' -and
            $pythonInfo.version -eq '3.9.13' -and $pythonInfo.architecture -eq '64bit')
        if (-not $pythonReady) {
            $runtimeCheck.Status = 'BLOCKED'
            $runtimeCheck.Summary = "Expected CPython 3.9.13 x64 with NumPy; found $($pythonInfo.implementation) $($pythonInfo.version) $($pythonInfo.architecture)."
        }
        else {
            $runtimeCheck.Summary = "CPython $($pythonInfo.version) x64; NumPy $($pythonInfo.numpy)."
        }
    }
    catch {
        $runtimeCheck.Status = 'BLOCKED'
        $runtimeCheck.Summary = "Runtime preflight output was not readable: $($_.Exception.Message)"
    }
}

$pythonChecks = @(
    [pscustomobject]@{
        id = 'm8-simulated-transport'
        name = 'M8 simulated selection and batch transport'
        arguments = @('-B', '-m', 'unittest', 'integration.m8_selection_transport.test_simulated_batch_consumer', 'integration.m8_selection_transport.test_simulated_selection_sender', '-v')
    },
    [pscustomobject]@{
        id = 'm8-selection-orchestration'
        name = 'M8 selection orchestration'
        arguments = @('-B', '-m', 'unittest', 'integration.test_m8_selection_orchestration', '-v')
    },
    [pscustomobject]@{
        id = 'm9-robot-adapter-contract'
        name = 'M9 logical object and robot adapter contract'
        arguments = @('-B', '-m', 'unittest', 'integration.test_m9_robot_adapter', '-v')
    },
    [pscustomobject]@{
        id = 'm9-mujoco-execution-contract'
        name = 'M9 logical scene binding and structured execution result contract'
        arguments = @('-B', '-m', 'unittest', 'integration.test_m9_mujoco_execution', '-v')
    },
    [pscustomobject]@{
        id = 'm9-virtual-block-contract'
        name = 'M9 Unity virtual block identity and explicit logical mapping'
        arguments = @('-B', '-m', 'unittest', 'integration.test_m9_virtual_block_contract', '-v')
    },
    [pscustomobject]@{
        id = 'm9-mujoco-smoke-cli'
        name = 'M9 optional headless smoke command interface'
        arguments = @('-B', '-m', 'integration.m9_mujoco_smoke', '--help')
    },
    [pscustomobject]@{
        id = 'm8-live-nd8-unit'
        name = 'M8 live-nd8 dry-run and mocked unit tests'
        arguments = @('-B', '-m', 'unittest', 'integration.test_m8_live_nd8', '-v')
    },
    [pscustomobject]@{
        id = 'm5-sync-protocol'
        name = 'M5 synchronization protocol tests'
        arguments = @('-B', '-m', 'unittest', 'discover', 'integration/synchronization/tests', '-v')
    },
    [pscustomobject]@{
        id = 'eeg-synthetic-offline'
        name = 'Deterministic synthetic EEG gate and offline-association tests'
        arguments = @('-B', '-m', 'unittest', 'eeg.decoder.tests.test_formal_online', 'eeg.sample_association.tests.test_offline_verify', '-v')
    }
)

foreach ($check in $pythonChecks) {
    if ($pythonReady) {
        [void](Invoke-NativeCheck -Id $check.id -Name $check.name -FilePath $PythonExecutable `
            -Arguments $check.arguments -RunDirectory $runDirectory -Timeout $TimeoutSeconds)
    }
    else {
        [void](Add-BlockedCheck -Id $check.id -Name $check.name `
            -Command (Format-Command $PythonExecutable $check.arguments) `
            -Summary 'Blocked because the required CPython 3.9.13 x64 + NumPy runtime preflight did not pass.' `
            -RunDirectory $runDirectory)
    }
}

$gitCommand = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$gitExecutable = if ($gitCommand) { $gitCommand.Source } else { $null }
$gitRootCheck = Invoke-NativeCheck -Id 'git-root' -Name 'Git repository root' -FilePath $gitExecutable `
    -Arguments @('rev-parse', '--show-toplevel') -RunDirectory $runDirectory -Timeout $TimeoutSeconds -FailureStatus 'BLOCKED'
$gitRoot = $null
$gitRootReady = $gitRootCheck.Status -eq 'PASS'
if ($gitRootReady) {
    $gitRootOutput = [System.IO.File]::ReadAllText($gitRootCheck.stdoutLogPath)
    $gitRoot = [System.IO.Path]::GetFullPath($gitRootOutput.Trim()).TrimEnd([char[]]@('\', '/'))
    $expectedRoot = $script:RepoRoot.TrimEnd([char[]]@('\', '/'))
    if ($gitRoot -ine $expectedRoot) {
        $gitRootReady = $false
        $gitRootCheck.Status = 'FAIL'
        $gitRootCheck.Summary = "Git root '$gitRoot' does not match verifier root '$expectedRoot'."
    }
    else {
        $gitRootCheck.Summary = "Git root matches verifier root: $gitRoot"
    }
}

$branchCheck = $null
$headCheck = $null
$diffCheck = $null
$statusCheck = $null
$branch = $null
$head = $null
$workingTreeClean = $null
$workingTreeStatus = @()
if ($gitRootReady) {
    $branchCheck = Invoke-NativeCheck -Id 'git-branch' -Name 'Current Git branch' -FilePath $gitExecutable `
        -Arguments @('branch', '--show-current') -RunDirectory $runDirectory -Timeout $TimeoutSeconds -FailureStatus 'BLOCKED'
    $headCheck = Invoke-NativeCheck -Id 'git-head' -Name 'Current Git HEAD' -FilePath $gitExecutable `
        -Arguments @('rev-parse', 'HEAD') -RunDirectory $runDirectory -Timeout $TimeoutSeconds -FailureStatus 'BLOCKED'
    $diffCheck = Invoke-NativeCheck -Id 'git-diff-check' -Name 'Git whitespace/error check' -FilePath $gitExecutable `
        -Arguments @('diff', '--check') -RunDirectory $runDirectory -Timeout $TimeoutSeconds
    $statusCheck = Invoke-NativeCheck -Id 'git-working-tree' -Name 'Git working-tree snapshot' -FilePath $gitExecutable `
        -Arguments @('status', '--short', '--branch', '--untracked-files=normal') -RunDirectory $runDirectory -Timeout $TimeoutSeconds

    if ($branchCheck.Status -eq 'PASS') { $branch = [System.IO.File]::ReadAllText($branchCheck.stdoutLogPath).Trim() }
    if ($headCheck.Status -eq 'PASS') { $head = [System.IO.File]::ReadAllText($headCheck.stdoutLogPath).Trim() }
    if ($statusCheck.Status -eq 'PASS') {
        $statusOutput = [System.IO.File]::ReadAllText($statusCheck.stdoutLogPath)
        $workingTreeStatus = @($statusOutput -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        $changes = @($workingTreeStatus | Where-Object { -not $_.StartsWith('##') })
        $workingTreeClean = $changes.Count -eq 0
        $conflicts = @($changes | Where-Object { $_.Length -ge 2 -and $_.Substring(0, 2) -match 'U|AA|DD' })
        if ($conflicts.Count -gt 0) {
            $statusCheck.Status = 'FAIL'
            $statusCheck.Summary = "Git reports $($conflicts.Count) unmerged path(s); working tree is not safe to continue."
        }
        elseif ($workingTreeClean) {
            $statusCheck.Summary = 'Working tree is clean.'
        }
        else {
            $statusCheck.Summary = "Working tree is dirty with $($changes.Count) changed path(s); recorded as informational."
        }
    }
}
else {
    $reason = 'Blocked because the verifier did not confirm this directory as the Git root.'
    [void](Add-BlockedCheck -Id 'git-branch' -Name 'Current Git branch' -Command 'git branch --show-current' -Summary $reason -RunDirectory $runDirectory)
    [void](Add-BlockedCheck -Id 'git-head' -Name 'Current Git HEAD' -Command 'git rev-parse HEAD' -Summary $reason -RunDirectory $runDirectory)
    [void](Add-BlockedCheck -Id 'git-diff-check' -Name 'Git whitespace/error check' -Command 'git diff --check' -Summary $reason -RunDirectory $runDirectory)
    [void](Add-BlockedCheck -Id 'git-working-tree' -Name 'Git working-tree snapshot' -Command 'git status --short --branch --untracked-files=normal' -Summary $reason -RunDirectory $runDirectory)
}

$failedCount = @($script:Checks | Where-Object { $_.status -eq 'FAIL' }).Count
$blockedCount = @($script:Checks | Where-Object { $_.status -eq 'BLOCKED' }).Count
$passedCount = @($script:Checks | Where-Object { $_.status -eq 'PASS' }).Count
if ($failedCount -gt 0) {
    $overallStatus = 'FAIL'
    $overallExitCode = 1
}
elseif ($blockedCount -gt 0) {
    $overallStatus = 'BLOCKED'
    $overallExitCode = 2
}
else {
    $overallStatus = 'PASS'
    $overallExitCode = 0
}

$summaryPath = Join-Path $runDirectory 'summary.json'
$summary = [pscustomobject]@{
    schemaVersion = 1
    profile = 'software-default-v1'
    runId = $runId
    overallStatus = $overallStatus
    exitCode = $overallExitCode
    startedAtUtc = $startedAtUtc
    completedAtUtc = [DateTime]::UtcNow.ToString('o')
    outputDirectory = $runDirectory
    summaryPath = $summaryPath
    repository = [pscustomobject]@{
        verifierRoot = $script:RepoRoot
        gitRoot = $gitRoot
        branch = $branch
        head = $head
        workingTreeClean = $workingTreeClean
        workingTreeStatus = $workingTreeStatus
    }
    pythonRuntime = [pscustomobject]@{
        executable = $PythonExecutable
        implementation = if ($pythonInfo) { $pythonInfo.implementation } else { $null }
        version = if ($pythonInfo) { $pythonInfo.version } else { $null }
        architecture = if ($pythonInfo) { $pythonInfo.architecture } else { $null }
        numpy = if ($pythonInfo) { $pythonInfo.numpy } else { $null }
    }
    safetyScope = @(
        'No Quest, ND8, COM port, ADB device, or robot operation is invoked.',
        'M8 socket tests use local loopback or socketpair endpoints only.',
        'Git checks are read-only; no reset, clean, checkout, commit, or push is run.'
    )
    notEnabledChecks = @(
        [pscustomobject]@{
            id = 'eeg-decoder-legacy-fbcca'
            command = (Format-Command $PythonExecutable @('-B', '-m', 'unittest', 'eeg.decoder.tests.test_decoder', '-v'))
            status = 'NOT_ENABLED'
            reason = 'The test module imports eeg.decoder.filter_realization, which requires SciPy; SciPy is absent in the configured runtime and was not installed.'
        },
        [pscustomobject]@{
            id = 'eeg-pseudo-online-all-backends'
            command = (Format-Command $PythonExecutable @('-B', '-m', 'unittest', 'eeg.decoder.tests.test_pseudo_online', '-v'))
            status = 'NOT_ENABLED'
            reason = 'The module exercises the legacy_fbcca backend and imports SciPy; it is excluded as a whole rather than counting a partial module as green.'
        }
    )
    checkCounts = [pscustomobject]@{ pass = $passedCount; fail = $failedCount; blocked = $blockedCount }
    checks = @($script:Checks)
}
[System.IO.File]::WriteAllText($summaryPath, ($summary | ConvertTo-Json -Depth 10), $script:Utf8NoBom)

Write-Output "Profile: software-default-v1"
Write-Output "Result: $overallStatus (PASS=$passedCount FAIL=$failedCount BLOCKED=$blockedCount; exit=$overallExitCode)"
if ($branch -or $head) { Write-Output "Git: branch=$branch HEAD=$head clean=$workingTreeClean" }
Write-Output 'Not enabled: EEG decoder suites requiring SciPy (see summary.json).'
foreach ($check in $script:Checks) {
    Write-Output ("{0,-28} {1,-7} {2}" -f $check.id, $check.status, $check.summary)
}
Write-Output "Summary: $summaryPath"
Write-Output "Logs: $runDirectory"
exit $overallExitCode
