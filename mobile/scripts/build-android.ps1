param(
    [string]$ProjectDirectory = (Split-Path $PSScriptRoot -Parent),
    [string]$OutputDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) 'artifacts\candidate'),
    [switch]$SkipPublish,
    [switch]$StrictPublish,
    [switch]$PublishExistingCandidate,
    [switch]$AllowSignerRotation,
    [string]$ExpectedSignerSha256,
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
$dotEnvPath = Join-Path $projectPath '.env'
if (Test-Path -LiteralPath $dotEnvPath) {
    Get-Content -LiteralPath $dotEnvPath | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), 'Process')
        }
    }
}
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

$appConfigPath = Join-Path $projectPath 'app.json'
$versionCode = 1
$versionName = "1.0.0"
if (Test-Path -LiteralPath $appConfigPath) {
    $config = (Get-Content -LiteralPath $appConfigPath -Raw | ConvertFrom-Json).expo
    if ($config.version) { $versionName = [string]$config.version }
    if ($config.android.versionCode) { $versionCode = [int]$config.android.versionCode }
    $gradlePath = Join-Path $projectPath 'android\app\build.gradle'
    if (Test-Path -LiteralPath $gradlePath) {
        $gradleContent = Get-Content -LiteralPath $gradlePath -Raw
        $gradleContent = [regex]::Replace($gradleContent, '(?m)^\s*versionCode\s+\d+', "        versionCode $versionCode")
        $gradleContent = [regex]::Replace($gradleContent, '(?m)^\s*versionName\s+"[^"]+"', "        versionName `"$versionName`"")
        Set-Content -LiteralPath $gradlePath -Value $gradleContent -NoNewline
    }
}

$releaseKeyConfig = if ($env:FT_RELEASE_CONFIG_FILE) {
    $env:FT_RELEASE_CONFIG_FILE
} else {
    Join-Path (Split-Path $repoRoot -Parent) '.release-keys\financial-twin-release.json'
}
$gradleReleaseArg = ''
if (Test-Path -LiteralPath $releaseKeyConfig -PathType Leaf) {
    $resolvedReleaseConfig = (Resolve-Path -LiteralPath $releaseKeyConfig).Path
    $env:FT_RELEASE_CONFIG_FILE = $resolvedReleaseConfig
    $gradleReleaseArg = " -PFT_RELEASE_CONFIG_FILE=`"$resolvedReleaseConfig`""
    if (!$ExpectedSignerSha256) {
        $cfgObj = Get-Content -LiteralPath $resolvedReleaseConfig -Raw | ConvertFrom-Json
        if ($cfgObj.signer_sha256) { $ExpectedSignerSha256 = [string]$cfgObj.signer_sha256 }
    }
    Write-Host "Assinatura privada de release habilitada via configuração local protegida." -ForegroundColor Cyan
}

if ($driveLetter -and [System.Environment]::OSVersion.Platform -eq 'Win32NT') {
    Write-Host "Mapeando unidade virtual $driveLetter -> $repoRoot para contornar limite de 260 caracteres do Ninja..." -ForegroundColor Cyan
    subst $driveLetter $repoRoot
    $createdSubst = $true
    $activeProject = Join-Path $driveLetter 'mobile'
}

$cxxDir = Join-Path $activeProject 'android\app\.cxx'
if (Test-Path -LiteralPath $cxxDir) {
    $staleNinja = Get-ChildItem -Path $cxxDir -Filter 'build.ninja' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (!$staleNinja -or ($driveLetter -and (Get-Content -LiteralPath $staleNinja.FullName -TotalCount 40 | Out-String) -notmatch [regex]::Escape($driveLetter))) {
        Remove-Item -Path $cxxDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Push-Location (Join-Path $activeProject 'android')
try {
    if (!$PublishExistingCandidate) {
        Remove-Item -Path (Join-Path $activeProject 'android\app\build\generated\assets\react\release') -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path (Join-Path $activeProject 'android\app\build\generated\sourcemaps\react\release') -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path (Join-Path $activeProject 'android\app\build\intermediates\assets\release') -Recurse -Force -ErrorAction SilentlyContinue
        $logPath = Join-Path $outputPath 'android-build.log'
        cmd.exe /c ".\gradlew.bat app:assembleRelease -x lintVitalRelease -x lintVitalAnalyzeRelease -PreactNativeArchitectures=arm64-v8a$gradleReleaseArg --max-workers=2 --console=plain --no-build-cache > `"$logPath`" 2>&1"
        if ($LASTEXITCODE -ne 0) { throw "Gradle falhou: $LASTEXITCODE. Consulte android-build.log." }
        $apk = Join-Path $activeProject 'android\app\build\outputs\apk\release\app-release.apk'
        if (!(Test-Path -LiteralPath $apk)) { throw 'O Gradle não produziu o APK esperado.' }
        Copy-Item -LiteralPath $apk -Destination $destination -Force
    } elseif (!(Test-Path -LiteralPath $destination)) {
        throw "Candidato APK não encontrado em $destination."
    }

    # Valida badging do APK gerado contra app.json
    $aapt = Join-Path $sdkPath 'build-tools\36.0.0\aapt.exe'
    if (Test-Path -LiteralPath $aapt) {
        $badging = & $aapt dump badging $destination
        $pkgMatch = $badging | Select-String "^package: name='com.financialtwin.app' versionCode='(\d+)' versionName='([^']+)'"
        if ($pkgMatch) {
            $builtCode = [int]$pkgMatch.Matches[0].Groups[1].Value
            $builtName = $pkgMatch.Matches[0].Groups[2].Value
            Write-Host "APK compilado verificado via aapt: v$builtName (build $builtCode)" -ForegroundColor Green
            if ($builtCode -ne $versionCode -or $builtName -ne $versionName) {
                throw "Divergência de versão no APK gerado: esperado v$versionName ($versionCode), mas o APK contém v$builtName ($builtCode)"
            }
        }
    }

    if (!$SkipPublish) {
        if ($StrictPublish) {
            $pubArgs = @{ ApkPath = $destination; BuildDirectory = $projectPath }
            if ($AllowSignerRotation) { $pubArgs['AllowSignerRotation'] = $true }
            if ($ExpectedSignerSha256) { $pubArgs['ExpectedSignerSha256'] = $ExpectedSignerSha256 }
            & (Join-Path $PSScriptRoot "publish-release.ps1") @pubArgs
        } else {
            $appConfigPath = Join-Path $projectPath 'app.json'
            $versionCode = 1
            $versionName = "1.0.0"
            if (Test-Path -LiteralPath $appConfigPath) {
                $config = (Get-Content -LiteralPath $appConfigPath -Raw | ConvertFrom-Json).expo
                if ($config.version) { $versionName = [string]$config.version }
                if ($config.android.versionCode) { $versionCode = [int]$config.android.versionCode }
            }

            $pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
            $apksigner = Join-Path $sdkPath 'build-tools\36.0.0\apksigner.bat'
            $signerSha = ''
            if (Test-Path -LiteralPath $apksigner) {
                $prevEap = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                $sigOut = & $apksigner verify --verbose --print-certs $destination 2>&1
                $sigExit = $LASTEXITCODE
                $ErrorActionPreference = $prevEap
                if ($sigExit -ne 0) { throw 'Assinatura Android inválida no APK compilado.' }
                $certLine = @($sigOut | ForEach-Object { "$_" } | Select-String '^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$')
                if ($certLine.Count -eq 1) {
                    $signerSha = $certLine[0].Matches[0].Groups[1].Value.ToUpperInvariant()
                }
                if ($ExpectedSignerSha256) {
                    $normExpected = ($ExpectedSignerSha256 -replace '[:\s]', '').ToUpperInvariant()
                    if ($signerSha -ne $normExpected) {
                        throw "O certificado do APK compilado ($signerSha) diverge do ExpectedSignerSha256 ($normExpected)."
                    }
                }
                if (Test-Path -LiteralPath $publishedAlias -PathType Leaf) {
                    $prevEap = $ErrorActionPreference
                    $ErrorActionPreference = 'Continue'
                    $prevSigOut = & $apksigner verify --verbose --print-certs $publishedAlias 2>&1
                    $prevSigExit = $LASTEXITCODE
                    $ErrorActionPreference = $prevEap
                    if ($prevSigExit -eq 0) {
                        $prevCertLine = @($prevSigOut | ForEach-Object { "$_" } | Select-String '^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$')
                        if ($prevCertLine.Count -eq 1) {
                            $prevSignerSha = $prevCertLine[0].Matches[0].Groups[1].Value.ToUpperInvariant()
                            if ($prevSignerSha -ne $signerSha -and !$AllowSignerRotation) {
                                throw "Rotação de certificado detectada ($prevSignerSha -> $signerSha). Use -AllowSignerRotation para confirmar."
                            }
                        }
                    }
                }
            }
            $sourceCommit = (& git -C $repoRoot rev-parse HEAD 2>$null)
            if ($sourceCommit) { $sourceCommit = $sourceCommit.Trim() } else { $sourceCommit = '' }

            # Salva versão arquivada e proveniência verificada antes de promover o alias
            $versionedApk = Join-Path $artifactsDir ("financial-twin-$versionName.apk")
            $provenancePath = [IO.Path]::ChangeExtension($versionedApk, '.provenance.json')
            $sourceVerifier = Join-Path $repoRoot 'scripts\verify_apk_source.py'
            $provenanceSha = ''
            if ((Test-Path -LiteralPath $pythonExe) -and (Test-Path -LiteralPath $sourceVerifier)) {
                & $pythonExe $sourceVerifier --apk $destination --build-project $activeProject --root $repoRoot --output $provenancePath --allow-dirty | Out-Null
                if ($LASTEXITCODE -ne 0) { throw 'Falha ao verificar a proveniência do bundle JS/nativo embutido no APK.' }
                $provenanceSha = (Get-FileHash -LiteralPath $provenancePath -Algorithm SHA256).Hash
            }

            Copy-Item -LiteralPath $destination -Destination $publishedAlias -Force
            Copy-Item -LiteralPath $publishedAlias -Destination $versionedApk -Force
            $fileHash = (Get-FileHash -LiteralPath $publishedAlias -Algorithm SHA256).Hash
            $fileSize = (Get-Item -LiteralPath $publishedAlias).Length
            $publishedAt = [DateTime]::UtcNow.ToString("o")

            # Atualiza manifest.json e apk-info.json
            $manifestPath = [IO.Path]::ChangeExtension($publishedAlias, '.manifest.json')
            $apkInfoPath = Join-Path $artifactsDir 'apk-info.json'
            
            $manifestData = [ordered]@{
                version_code = $versionCode
                version_name = $versionName
                sha256 = $fileHash
                size_bytes = $fileSize
                published_at = $publishedAt
                source_commit = $sourceCommit
                signer_sha256 = $signerSha
                provenance_sha256 = $provenanceSha
                download_url = "/v1/app-update/apk"
            }
            
            $jsonString = $manifestData | ConvertTo-Json
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($manifestPath, $jsonString, $utf8NoBom)
            [System.IO.File]::WriteAllText($apkInfoPath, $jsonString, $utf8NoBom)
            
            Write-Host "APK publicado com sucesso em: $publishedAlias" -ForegroundColor Green
            Write-Host "Metadados gerados: v$versionName (build $versionCode), Tamanho: $fileSize bytes, SHA256: $fileHash" -ForegroundColor Cyan

            # Atualiza ponteiro de release no README.md
            $pointerScript = Join-Path $repoRoot 'scripts\release_pointer.py'
            $readmePath = Join-Path $repoRoot 'README.md'
            if ((Test-Path -LiteralPath $pythonExe) -and (Test-Path -LiteralPath $pointerScript) -and (Test-Path -LiteralPath $readmePath)) {
                & $pythonExe $pointerScript --manifest $manifestPath --app-config $appConfigPath --readme $readmePath
            }

            # Commit e Push no GitHub (somente manifesto de versão e ponteiro; nunca telemetria)
            if (!$NoPush) {
                Write-Host "Executando git commit e push para o GitHub..." -ForegroundColor Cyan
                Push-Location $repoRoot
                try {
                    git add mobile/app.json README.md
                    $commitMsg = "feat(release): publicar APK v$versionName (build $versionCode)"
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
