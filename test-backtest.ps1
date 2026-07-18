param(
    [switch]$Full
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }

Push-Location $projectRoot
try {
    if ($Full) {
        & $python -m pytest -q
    }
    else {
        & $python -m pytest `
            bank_valuation/tests/test_strategy_backtest.py `
            bank_valuation/tests/test_cross_industry.py `
            bank_valuation/tests/test_backtest_regression.py `
            -q
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
