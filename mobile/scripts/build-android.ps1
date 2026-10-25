param(
    [string]$ProjectDirectory = (Split-Path $PSScriptRoot -Parent),
    [string]$OutputDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) 'artifacts\candidate'),
    [switch]$SkipPublish,
    [switch]$StrictPublish,
    [switch]$NoPush
)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath $ProjectDirectory).Path
$sdkPath = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
$javaPath = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { Join-Path $env:ProgramFiles 'Android\Android Studio\jbr' }
if (!(Test-Path -LiteralPath (Join-Path $javaPath 'bin\java.exe'))) { throw 'Java não encontrado. Defina JAVA_HOME.' }
if (!(Test-Path -LiteralPath (Join-Path $sdkPath 'platforms\android-36'))) { throw 'Android SDK 36 não encontrado. Defina ANDROID_HOME.' }
if (!(Test-Path -LiteralPath (Join-Path $projectPath 'node_modules\expo\package.json'))) { throw 'Execute pnpm install --frozen-lockfile antes de compilar.' }
$env:JAVA_HOME = $javaPath
$env:ANDROID_HOME = $sdkPath
$env:ANDROID_SDK_ROOT = $sdkPath
$env:PATH = "$javaPath\bin;$env:PATH"
$env:NODE_ENV = 'production'
if ($env:JAVA_TOOL_OPTIONS -notmatch '--enable-native-access') {
    $env:JAVA_TOOL_OPTIONS = "$env:JAVA_TOOL_OPTIONS --enable-native-access=ALL-UNNAMED".Trim()
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputPath = (Resolve-Path -LiteralPath $OutputDirectory).Path
$destination = Join-Path $outputPath 'financial-twin-arm64.apk'
$artifactsDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'artifacts'
$publishedAlias = [IO.Path]::GetFullPath((Join-Path $artifactsDir 'financial-twin-arm64.apk'))
if([IO.Path]::GetFullPath($destination) -eq $publishedAlias){throw 'Use um OutputDirectory de staging; o build não pode sobrescrever o APK publicado.'}

# Bypassa o limite de 260 caracteres do Ninja no Windows mapeando temporariamente uma unidade virtual
$driveLetter = $null
$createdSubst = $false
$usedDrives = Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Name.ToUpper() }
foreach ($letter in [char[]]([int][char]'Z'..[int][char]'E')) {
    $cand = [string]$letter
    if ($usedDrives -notcontains $cand) {
        $driveLetter = "$cand`:"
        break
    }
}

$repoRoot = Split-Path (Resolve-Path $projectPath).Path -Parent
$activeProject = $projectPath

if ($driveLetter -and [System.Environment]::OSVersion.Platform -eq 'Win32NT') {
    Write-Host "Mapeando unidade virtual $driveLetter -> $repoRoot para contornar limite de 260 caracteres do Ninja..." -ForegroundColor Cyan
    subst $driveLetter $repoRoot
    $createdSubst = $true
    $activeProject = Join-Path $driveLetter 'mobile'
}

Remove-Item -Path (Join-Path $activeProject 'android\app\.cxx') -Recurse -Force -ErrorAction SilentlyContinue
Push-Location (Join-Path $activeProject 'android')
try {
    $logPath = Join-Path $outputPath 'android-build.log'
    cmd.exe /c ".\gradlew.bat app:assembleRelease -x lintVitalRelease -x lintVitalAnalyzeRelease -PreactNativeArchitectures=arm64-v8a --max-workers=2 --console=plain --build-cache > `"$logPath`" 2>&1"
    if ($LASTEXITCODE -ne 0) { throw "Gradle falhou: $LASTEXITCODE. Consulte android-build.log." }
    $apk = Join-Path $activeProject 'android\app\build\outputs\apk\release\app-release.apk'
    if (!(Test-Path -LiteralPath $apk)) { throw 'O Gradle não produziu o APK esperado.' }
    Copy-Item -LiteralPath $apk -Destination $destination -Force

    if (!$SkipPublish) {
        if ($StrictPublish) {
            & (Join-Path $PSScriptRoot "publish-release.ps1") -ApkPath $destination -BuildDirectory $projectPath
        } else {
            # Publicação direta no estilo SpamShield (sem bloqueios do git ou gates de proveniência complexos)
            Copy-Item -LiteralPath $destination -Destination $publishedAlias -Force
            
            $appConfigPath = Join-Path $projectPath 'app.json'
            $versionCode = 1
            $versionName = "1.0.0"
            if (Test-Path -LiteralPath $appConfigPath) {
                $config = (Get-Content -LiteralPath $appConfigPath -Raw | ConvertFrom-Json).expo
                if ($config.version) { $versionName = [string]$config.version }
                if ($config.android.versionCode) { $versionCode = [int]$config.android.versionCode }
            }
            
            $fileHash = (Get-FileHash -LiteralPath $publishedAlias -Algorithm SHA256).Hash
            $fileSize = (Get-Item -LiteralPath $publishedAlias).Length
            $publishedAt = [DateTime]::UtcNow.ToString("o")

            # Salva versão arquivada
            $versionedApk = Join-Path $artifactsDir ("financial-twin-$versionName.apk")
            Copy-Item -LiteralPath $publishedAlias -Destination $versionedApk -Force

            # Atualiza manifest.json e apk-info.json
            $manifestPath = [IO.Path]::ChangeExtension($publishedAlias, '.manifest.json')
            $apkInfoPath = Join-Path $artifactsDir 'apk-info.json'
            
            $manifestData = [ordered]@{
                version_code = $versionCode
                version_name = $versionName
                sha256 = $fileHash
                size_bytes = $fileSize
                published_at = $publishedAt
                download_path = "/apk"
            }
            
            $jsonString = $manifestData | ConvertTo-Json
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($manifestPath, $jsonString, $utf8NoBom)
            [System.IO.File]::WriteAllText($apkInfoPath, $jsonString, $utf8NoBom)
            
            Write-Host "APK publicado com sucesso (estilo SpamShield) em: $publishedAlias" -ForegroundColor Green
            Write-Host "Metadados gerados: v$versionName (build $versionCode), Tamanho: $fileSize bytes, SHA256: $fileHash" -ForegroundColor Cyan

            $pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'

            # 1. Sincroniza telemetria (unificado em perf_telemetry.jsonl; migra .live.jsonl se existir)
            $telemetryLive = Join-Path $repoRoot 'logs\perf_telemetry.live.jsonl'
            $telemetryMain = Join-Path $repoRoot 'logs\perf_telemetry.jsonl'
            if (Test-Path -LiteralPath $telemetryLive) {
                Write-Host "Consolidando telemetria legada em $telemetryMain..." -ForegroundColor Cyan
                if (Test-Path -LiteralPath $pythonExe) {
                    & $pythonExe -c "
from pathlib import Path
target = Path(r'$telemetryMain')
live = Path(r'$telemetryLive')
if live.exists():
    existing = set(target.read_text(encoding='utf-8', errors='ignore').splitlines()) if target.exists() else set()
    new_lines = [l.strip() for l in live.read_text(encoding='utf-8', errors='ignore').splitlines() if l.strip() and l.strip() not in existing]
    if new_lines:
        with target.open('a', encoding='utf-8') as f:
            for l in new_lines: f.write(l + '\n')
        print(f'Telemetria: {len(new_lines)} novos eventos consolidados.')
    live.unlink(missing_ok=True)
"
                }
            }

            # 2. Atualiza ponteiro de release no README.md
            $pointerScript = Join-Path $repoRoot 'scripts\release_pointer.py'
            $readmePath = Join-Path $repoRoot 'README.md'
            if ((Test-Path -LiteralPath $pythonExe) -and (Test-Path -LiteralPath $pointerScript) -and (Test-Path -LiteralPath $readmePath)) {
                & $pythonExe $pointerScript --manifest $manifestPath --app-config $appConfigPath --readme $readmePath
            }

            # 3. Commit e Push no GitHub (incluindo telemetria e ponteiro da release)
            if (!$NoPush) {
                Write-Host "Executando git commit e push para o GitHub..." -ForegroundColor Cyan
                Push-Location $repoRoot
                try {
                    git add logs/perf_telemetry.jsonl mobile/app.json README.md
                    $commitMsg = "feat(release): publicar APK v$versionName (build $versionCode) e sincronizar telemetria"
                    git commit -m $commitMsg
                    if ($LASTEXITCODE -eq 0 -or (git status --porcelain).Trim() -eq "") {
                        git push origin HEAD
                        if ($LASTEXITCODE -eq 0) {
                            Write-Host "Release e telemetria enviados com sucesso para o GitHub!" -ForegroundColor Green
                        } else {
                            Write-Warning "Falha no git push. Verifique conexão e credenciais do repositório."
                        }
                    }
                } catch {
                    Write-Warning "Aviso: não foi possível concluir commit/push: $_"
                } finally {
                    Pop-Location
                }
            }
        }
    }
    Get-FileHash -LiteralPath $destination -Algorithm SHA256
    Write-Output "APK pronto: $destination"
} finally {
    Pop-Location
    if ($createdSubst -and $driveLetter) {
        subst $driveLetter /d
    }
}
