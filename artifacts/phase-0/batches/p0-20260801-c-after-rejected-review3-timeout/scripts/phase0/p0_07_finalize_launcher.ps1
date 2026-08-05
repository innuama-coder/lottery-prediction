$ErrorActionPreference = "Stop"
if ($args.Count -ne 0) { [Console]::Error.WriteLine('{"status":"FAIL","error":"arguments are forbidden"}'); exit 2 }
$repoRoot=(Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
try {
    $verification=Get-Content -Raw -LiteralPath (Join-Path $repoRoot "artifacts\phase-0\verification-command.json") | ConvertFrom-Json
    $interpreter=[string]$verification.interpreter_path
    if (-not (Test-Path -LiteralPath $interpreter -PathType Leaf)) { throw "frozen interpreter unavailable" }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $interpreter).Hash.ToLowerInvariant() -ne [string]$verification.interpreter_sha256) { throw "frozen interpreter hash mismatch" }
    Push-Location -LiteralPath $repoRoot
    try { & $interpreter "scripts/phase0/p0_07_finalize.py"; exit $LASTEXITCODE } finally { Pop-Location }
} catch {
    $body=[ordered]@{status="FAIL";error=$_.Exception.Message} | ConvertTo-Json -Compress
    [Console]::Error.WriteLine($body); exit 2
}
