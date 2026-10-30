$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectRoot 'data\backend-startup'

try {
    if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Python do backend não encontrado.' }
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $logStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $env:PYTHONUNBUFFERED = '1'
    $backendProcess = Start-Process -FilePath $pythonPath -ArgumentList 'scripts\proxy_to_vps.py' `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDirectory "$logStamp.out.log") `
        -RedirectStandardError (Join-Path $logDirectory "$logStamp.err.log")
    $backendProcess.WaitForExit()
    # Duplicate supervisors exit successfully. Unexpected exit is retried.
    exit $backendProcess.ExitCode
} catch {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Add-Content -LiteralPath (Join-Path $logDirectory 'launcher.err.log') `
        -Value ("{0:o} {1}" -f (Get-Date), $_.Exception.Message)
    exit 1
}
