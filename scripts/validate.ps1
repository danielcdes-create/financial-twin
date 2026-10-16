param([switch]$Integration,[switch]$WebExport,[string]$TestMongoUri)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
$oldTestUri=$env:MONGODB_URI;$oldDirectHosts=$env:MONGODB_DIRECT_HOSTS
try {
  if($Integration){
    $env:MONGODB_URI=if($TestMongoUri){$TestMongoUri}elseif($oldTestUri){$oldTestUri}else{'mongodb://127.0.0.1:27017/?replicaSet=financialTwinTest'}
    $env:MONGODB_DIRECT_HOSTS=''
    & .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'backend'); from app.config import get_settings; from app.synthetic_testing import require_synthetic_target; require_synthetic_target(get_settings().mongodb_uri)"
    if($LASTEXITCODE -ne 0){throw 'Synthetic integration target rejected before any writes'}
  }
  & .\.venv\Scripts\python.exe -m unittest discover -s backend/tests -q
  if($LASTEXITCODE -ne 0){throw 'Backend regression suite failed'}
  & .\.venv\Scripts\python.exe -m pip_audit -r backend/requirements.lock.txt --no-deps --disable-pip
  if($LASTEXITCODE -ne 0){throw 'Backend dependency security gate failed'}
  if($Integration){
    $evidencePath=Join-Path $projectRoot ('data\test-evidence\'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
    & .\.venv\Scripts\python.exe backend/scripts/check_all.py $evidencePath
    if($LASTEXITCODE -ne 0){throw 'Isolated integration suite failed'}
  }
  Push-Location mobile
  try {
    & node scripts/run-client-tests.cjs
    if($LASTEXITCODE -ne 0){throw 'Client regression suite failed'}
    & pnpm exec expo install --check
    if($LASTEXITCODE -ne 0){throw 'Expo SDK compatibility failed'}
    & node scripts/audit-dependencies.cjs
    if($LASTEXITCODE -ne 0){throw 'Dependency security gate failed'}
    & pnpm exec tsc --noEmit
    if($LASTEXITCODE -ne 0){throw 'Typecheck failed'}
    & pnpm exec eslint App.tsx components utils app
    if($LASTEXITCODE -ne 0){throw 'Source lint failed'}
    if($WebExport){
      & pnpm exec expo export --platform web --output-dir desktop-dist
      if($LASTEXITCODE -ne 0){throw 'Web export failed'}
    }
  } finally {Pop-Location}
} finally {$env:MONGODB_URI=$oldTestUri;$env:MONGODB_DIRECT_HOSTS=$oldDirectHosts;Pop-Location}
