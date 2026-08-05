[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $VerifierArguments
)

$ErrorActionPreference = 'Stop'
$BundledPython = 'C:\Users\54256213\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$Interpreter = if ($env:PHASE0_PYTHON) { $env:PHASE0_PYTHON } else { $BundledPython }

if (-not [System.IO.Path]::IsPathRooted($Interpreter) -or $Interpreter -notmatch '^(?:[A-Za-z]:\\|\\\\)') {
    Write-Error 'PHASE0_PYTHON must be an absolute path.'
    exit 64
}
if (-not (Test-Path -LiteralPath $Interpreter -PathType Leaf)) {
    Write-Error "Frozen Phase 0 Python interpreter not found: $Interpreter"
    exit 69
}

& $Interpreter 'scripts/phase0/verify_phase0.py' @VerifierArguments
exit $LASTEXITCODE
