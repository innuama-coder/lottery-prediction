[CmdletBinding()]
param(
    [ValidateSet('Verify', 'Install', 'Uninstall')]
    [string]$Action = 'Verify',
    [switch]$DryRun,
    [string]$AuditPath
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Artifacts = Join-Path $RepoRoot 'artifacts\phase-0'
$FixedTaskName = 'AutoresearchLotte-P0-06'
if ($AuditPath -and $Action -ne 'Verify') { throw '-AuditPath is only valid with -Action Verify.' }
if ($Action -eq 'Uninstall') {
    if ($DryRun) { [pscustomobject]@{ Action='Uninstall'; TaskName=$FixedTaskName; DryRun=$true; Mutated=$false }; exit 0 }
    $existingForRemoval = Get-ScheduledTask -TaskName $FixedTaskName -ErrorAction SilentlyContinue
    if ($null -eq $existingForRemoval) { [pscustomobject]@{ Action='Uninstall'; TaskName=$FixedTaskName; Mutated=$false; AlreadyAbsent=$true }; exit 0 }
    Unregister-ScheduledTask -TaskName $FixedTaskName -Confirm:$false -ErrorAction Stop
    [pscustomobject]@{ Action='Uninstall'; TaskName=$FixedTaskName; Mutated=$true }
    exit 0
}
$PlanPath = Join-Path $Artifacts 'p0-06-runtime-plan.json'
$PlanHashPath = Join-Path $Artifacts 'p0-06-runtime-plan.json.sha256'
$Verification = Get-Content -Raw (Join-Path $Artifacts 'verification-command.json') | ConvertFrom-Json
$Plan = Get-Content -Raw $PlanPath | ConvertFrom-Json
$TaskName = [string]$Plan.scheduler.task_name
$Python = [System.IO.Path]::GetFullPath([string]$Verification.interpreter_path)
$Runner = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot 'scripts\phase0\p0_06_runner.py'))
$SoakLogPath = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot ([string]$Plan.soak_log_path)))
$PlanSha256 = (Get-Content -Raw $PlanHashPath).Trim()
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value

function Resolve-AccountSid([string]$AccountName) {
    try {
        $account = [System.Security.Principal.NTAccount]::new($AccountName)
        return $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    catch {
        throw "Cannot resolve scheduled-task principal '$AccountName' to a SID: $($_.Exception.Message)"
    }
}

if ($Plan.status -ne 'prepared_not_started' -or $Plan.scheduler.trigger_count -ne 24 -or $Plan.scheduler.triggers.Count -ne 24) {
    throw 'Runtime plan is not the frozen prepared_not_started 24-trigger plan.'
}
if (-not ([System.IO.Path]::IsPathRooted($Python)) -or -not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $Runner)) {
    throw 'Absolute interpreter or runner path is unavailable.'
}
if ($PlanSha256 -notmatch '^[0-9a-f]{64}$') { throw 'Runtime plan hash sidecar is malformed.' }
& $Python $Runner --action verify-plan --artifacts $Artifacts --expected-plan-sha256 $PlanSha256 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Runtime plan failed offline schema/semantic/hash validation.' }

$TriggerSpecs = @($Plan.scheduler.triggers | ForEach-Object {
    $utc = [DateTimeOffset]::Parse([string]$_.planned_at_utc).ToUniversalTime()
    $local = [DateTimeOffset]::Parse([string]$_.local_at)
    if ($local.Offset.TotalHours -ne 8 -or $local.ToUniversalTime() -ne $utc) { throw "Trigger conversion mismatch: $($_.request_id)" }
    [pscustomobject]@{ RequestId = $_.request_id; At = $local.DateTime; PlannedUtc = $_.planned_at_utc; LocalAt = $_.local_at }
})

$ExpectedArguments = '"{0}" --action execute-due --artifacts "{1}" --allow-network --expected-plan-sha256 {2}' -f $Runner, $Artifacts, $PlanSha256

function Get-FrozenTaskAudit($ExistingTask) {
    $actualActions = @($ExistingTask.Actions)
    $actualTriggers = @($ExistingTask.Triggers)
    $expectedStarts = @($TriggerSpecs | ForEach-Object { $_.At.ToString('yyyy-MM-ddTHH:mm:ss') } | Sort-Object)
    $actualStarts = @($actualTriggers | ForEach-Object { ([DateTime]$_.StartBoundary).ToString('yyyy-MM-ddTHH:mm:ss') } | Sort-Object)
    try { $executionLimit = [System.Xml.XmlConvert]::ToTimeSpan([string]$ExistingTask.Settings.ExecutionTimeLimit) }
    catch { $executionLimit = [TimeSpan]::Parse([string]$ExistingTask.Settings.ExecutionTimeLimit) }
    $actualPrincipalSid = Resolve-AccountSid ([string]$ExistingTask.Principal.UserId)
    $checks = [ordered]@{
        ActionCountOne = ($actualActions.Count -eq 1)
        ExecuteExact = ([string]$actualActions[0].Execute -eq $Python)
        ArgumentsExact = ([string]$actualActions[0].Arguments -eq $ExpectedArguments)
        WorkingDirectoryExact = ([string]$actualActions[0].WorkingDirectory -eq $RepoRoot)
        TriggerCount24 = ($actualTriggers.Count -eq 24)
        TriggerTimesExact = (($actualStarts -join '|') -eq ($expectedStarts -join '|'))
        StartWhenAvailable = [bool]$ExistingTask.Settings.StartWhenAvailable
        ExecutionTimeLimit15Minutes = ($executionLimit.TotalMinutes -eq 15)
        MultipleInstancesIgnoreNew = ([string]$ExistingTask.Settings.MultipleInstances -eq 'IgnoreNew')
        PrincipalCurrentSid = ($actualPrincipalSid -eq $CurrentSid)
        PrincipalInteractive = ([string]$ExistingTask.Principal.LogonType -in @('Interactive', 'InteractiveToken'))
        PrincipalLimited = ([string]$ExistingTask.Principal.RunLevel -eq 'Limited')
    }
    [pscustomobject]@{ Matches=(-not (@($checks.Values) -contains $false)); Checks=[pscustomobject]$checks; ActualTriggers=$actualTriggers.Count }
}

function Write-InstallAudit($ExistingTask, $FrozenTaskAudit) {
    if (-not $AuditPath) { return }

    $expectedAuditPath = [System.IO.Path]::GetFullPath((Join-Path $Artifacts 'p0-06-scheduler-install-audit.json'))
    $resolvedAuditPath = if ([System.IO.Path]::IsPathRooted($AuditPath)) {
        [System.IO.Path]::GetFullPath($AuditPath)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $AuditPath))
    }
    if (-not [string]::Equals($resolvedAuditPath, $expectedAuditPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "AuditPath must resolve to the canonical auxiliary artifact: $expectedAuditPath"
    }

    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
    $nextRun = [DateTime]$taskInfo.NextRunTime
    if ($nextRun -eq [DateTime]::MinValue) { throw 'Installed task has no next run time.' }
    $nextRunLocal = $nextRun.ToString('yyyy-MM-ddTHH:mm:ss') + '+08:00'
    $lastRun = [DateTime]$taskInfo.LastRunTime
    $lastRunState = if ($lastRun -eq [DateTime]::MinValue -or $lastRun.Year -le 2000) { 'never_run' } else { 'has_run' }

    $evidencePath = Join-Path $Artifacts 'evidence-manifest.jsonl'
    $evidenceFile = Get-Item -LiteralPath $evidencePath -ErrorAction Stop
    $soakFile = Get-Item -LiteralPath $SoakLogPath -ErrorAction Stop
    $verifyCommand = 'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/phase0/install_p0_06_scheduled_task.ps1 -Action Verify -AuditPath artifacts/phase-0/p0-06-scheduler-install-audit.json'
    $exitCode = if ($FrozenTaskAudit.Matches) { 0 } else { 1 }
    $record = [ordered]@{
        schema_version = '1.0.0'
        artifact_type = 'p0_06_scheduler_install_audit'
        contract_version = '1.3'
        recorded_at_utc = [DateTime]::UtcNow.ToString('o')
        task_name = $TaskName
        scope = 'current_user'
        runtime_plan_sha256 = $PlanSha256
        installed = $true
        matches_frozen_plan = [bool]$FrozenTaskAudit.Matches
        checks = $FrozenTaskAudit.Checks
        trigger_count = [int]$FrozenTaskAudit.ActualTriggers
        next_run_local = $nextRunLocal
        last_run_state = $lastRunState
        last_task_result = [int]$taskInfo.LastTaskResult
        missed_runs = [int]$taskInfo.NumberOfMissedRuns
        soak_log_bytes = [int64]$soakFile.Length
        evidence_manifest_sha256 = (Get-FileHash -LiteralPath $evidencePath -Algorithm SHA256).Hash.ToLowerInvariant()
        evidence_manifest_last_write_utc = $evidenceFile.LastWriteTimeUtc.ToString('o')
        verify_command = $verifyCommand
        exit_code = $exitCode
        os_state_claim_scope = 'point_in_time_snapshot_not_continuous_os_proof'
    }
    $json = ($record | ConvertTo-Json -Depth 8) + "`n"
    [System.IO.File]::WriteAllText($resolvedAuditPath, $json, [System.Text.UTF8Encoding]::new($false))
}

if ($Action -eq 'Verify') {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        if ($AuditPath) { throw 'Cannot write an install audit because the scheduled task is not installed.' }
        [pscustomobject]@{ Action='Verify'; TaskName=$TaskName; Installed=$false; MatchesFrozenPlan=$false; ExpectedTriggers=24; Mutated=$false; Python=$Python; Runner=$Runner; SoakLogPath=$SoakLogPath }
        exit 0
    }
    $audit = Get-FrozenTaskAudit $existing
    Write-InstallAudit $existing $audit
    [pscustomobject]@{ Action='Verify'; TaskName=$TaskName; Installed=$true; MatchesFrozenPlan=$audit.Matches; Checks=$audit.Checks; ExpectedTriggers=24; ActualTriggers=$audit.ActualTriggers; Mutated=$false; PlanSha256=$PlanSha256; Python=$Python; Runner=$Runner; SoakLogPath=$SoakLogPath }
    if (-not $audit.Matches) { exit 1 }
    exit 0
}

$Argument = $ExpectedArguments
if ($DryRun) {
    [pscustomobject]@{ Action='Install'; TaskName=$TaskName; DryRun=$true; Mutated=$false; TriggerCount=$TriggerSpecs.Count; Python=$Python; Arguments=$Argument; WorkingDirectory=$RepoRoot; StartWhenAvailable=$true }
    exit 0
}

$existingBeforeInstall = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existingBeforeInstall) {
    $audit = Get-FrozenTaskAudit $existingBeforeInstall
    if (-not $audit.Matches) { throw 'An existing task with this name does not match the frozen P0-06 definition; refusing to overwrite it.' }
    [pscustomobject]@{ Action='Install'; TaskName=$TaskName; Mutated=$false; Idempotent=$true; MatchesFrozenPlan=$true; Checks=$audit.Checks }
    exit 0
}

$Triggers = @($TriggerSpecs | ForEach-Object { New-ScheduledTaskTrigger -Once -At $_.At })
$TaskAction = New-ScheduledTaskAction -Execute $Python -Argument $Argument -WorkingDirectory $RepoRoot
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentIdentity -LogonType Interactive -RunLevel Limited
$Task = New-ScheduledTask -Action $TaskAction -Trigger $Triggers -Settings $Settings -Principal $Principal -Description 'P0-06 exact frozen observation schedule; catch-up runs retain planned and actual timestamps.'
Register-ScheduledTask -TaskName $TaskName -InputObject $Task | Out-Null
[pscustomobject]@{ Action='Install'; TaskName=$TaskName; Mutated=$true; TriggerCount=$Triggers.Count; StartWhenAvailable=$true }
