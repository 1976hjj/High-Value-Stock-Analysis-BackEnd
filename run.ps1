# Start both sibling projects. Occupied ports 8000 and 5173 are forcibly freed.
$ErrorActionPreference = 'Stop'

$backendDir = $PSScriptRoot
$frontendDir = Join-Path (Split-Path -Parent $backendDir) 'High-Value-Stock-Analysis-FrontEnd'
$python = Join-Path $backendDir '.venv\Scripts\python.exe'
$node = Join-Path $frontendDir '.local-tools\node-v24.18.0-win-x64\node.exe'
$vite = Join-Path $frontendDir 'node_modules\vite\bin\vite.js'

foreach ($requiredPath in @($python, $vite)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Missing dependency: $requiredPath. Install the project's dependencies first."
    }
}
if (-not (Test-Path -LiteralPath $node -PathType Leaf)) {
    $node = (Get-Command node.exe -ErrorAction Stop).Source
}

# Check the Python environment before stopping any running services.
Push-Location $backendDir
try {
    & $python -c 'import fastapi, uvicorn; from bank_valuation.app.main import app'
    if ($LASTEXITCODE -ne 0) {
        throw 'Backend dependencies failed to load. Run .\.venv\Scripts\python.exe -m pip install -r requirements.txt.'
    }
}
finally {
    Pop-Location
}

function Get-PortOwners {
    param([int]$Port)
    Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalPort -eq $Port } |
        Select-Object -ExpandProperty OwningProcess -Unique
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    if ($ProcessId -le 4 -or $ProcessId -eq $PID) {
        throw "Refusing to stop system/current process $ProcessId."
    }
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        $result = & "$env:SystemRoot\System32\taskkill.exe" /PID $ProcessId /T /F 2>&1
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            throw "Could not stop process ${ProcessId}: $result"
        }
    }
}

function Clear-Port {
    param([int]$Port)
    foreach ($ownerId in @(Get-PortOwners -Port $Port)) {
        Write-Host "Stopping PID $ownerId and its children on port $Port..."
        Stop-ProcessTree -ProcessId $ownerId
    }
    $deadline = (Get-Date).AddSeconds(10)
    while (@(Get-PortOwners -Port $Port).Count -gt 0) {
        if ((Get-Date) -ge $deadline) {
            throw "Port $Port is still occupied."
        }
        Start-Sleep -Milliseconds 250
    }
}

function Wait-Service {
    param([string]$Url, [System.Diagnostics.Process]$ServiceProcess)
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $ServiceProcess.Refresh()
        if ($ServiceProcess.HasExited) {
            throw "Service exited before becoming ready: $Url. Check the startup logs."
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                return
            }
        }
        catch {
            # The server may still be importing or compiling on its first start.
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "Service did not become ready within 30 seconds: $Url. Check the startup logs."
}

$logDir = Join-Path $backendDir ('logs\run\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Write-Host "Logs: $logDir"

Clear-Port -Port 8000
Clear-Port -Port 5173

$backendProcess = $null
$frontendProcess = $null
try {
    $backendProcess = Start-Process -FilePath $python -WorkingDirectory $backendDir `
        -ArgumentList @('-m', 'uvicorn', 'bank_valuation.app.main:app', '--reload', '--host', '127.0.0.1', '--port', '8000') `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDir 'backend.out.log') `
        -RedirectStandardError (Join-Path $logDir 'backend.err.log')

    $frontendProcess = Start-Process -FilePath $node -WorkingDirectory $frontendDir `
        -ArgumentList @(('"' + $vite + '"'), '--host', '127.0.0.1', '--port', '5173', '--strictPort') `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDir 'frontend.out.log') `
        -RedirectStandardError (Join-Path $logDir 'frontend.err.log')

    Wait-Service -Url 'http://127.0.0.1:8000/health' -ServiceProcess $backendProcess
    Wait-Service -Url 'http://127.0.0.1:5173/' -ServiceProcess $frontendProcess
    $universe = Invoke-RestMethod -Uri 'http://127.0.0.1:5173/api/strategy/universe' -TimeoutSec 10
    if (-not $universe.industry_count -or -not $universe.stock_count) {
        throw 'Frontend-to-backend API proxy verification failed.'
    }
}
catch {
    foreach ($startedProcess in @($frontendProcess, $backendProcess)) {
        if ($null -ne $startedProcess) {
            try {
                Stop-ProcessTree -ProcessId $startedProcess.Id
            }
            catch {
                Write-Warning $_.Exception.Message
            }
        }
    }
    Write-Host "Startup failed. Logs: $logDir" -ForegroundColor Red
    throw
}

Write-Host ''
Write-Host 'Both projects are running in the background.' -ForegroundColor Green
Write-Host 'Frontend: http://127.0.0.1:5173/'
Write-Host 'API docs: http://127.0.0.1:8000/docs'
Write-Host "API proxy OK: $($universe.industry_count) industries, $($universe.stock_count) stocks."
Write-Host 'Run this script again to restart both projects.'
