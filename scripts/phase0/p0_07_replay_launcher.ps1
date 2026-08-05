$ErrorActionPreference = "Stop"

if ($args.Count -ne 0) {
    [Console]::Error.WriteLine('{"status":"FAIL","error":"arguments are forbidden"}')
    exit 2
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$verificationPath = Join-Path $repoRoot "artifacts\phase-0\verification-command.json"
$driverRef = "scripts/phase0/p0_07_replay_driver.py"

try {
    $verification = Get-Content -Raw -LiteralPath $verificationPath | ConvertFrom-Json
    $interpreter = [string]$verification.interpreter_path
    if (-not (Test-Path -LiteralPath $interpreter -PathType Leaf)) {
        throw "frozen interpreter is unavailable"
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $interpreter).Hash.ToLowerInvariant()
    if ($actualHash -ne [string]$verification.interpreter_sha256) {
        throw "frozen interpreter hash mismatch"
    }
    Push-Location -LiteralPath $repoRoot
    try {
        & $interpreter $driverRef
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
catch {
    $message = $_.Exception.Message.Replace('"', '\"')
    [Console]::Error.WriteLine("{`"status`":`"FAIL`",`"error`":`"$message`"}")
    exit 2
}
