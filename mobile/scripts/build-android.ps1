param(
    [string]$ProjectDirectory = (Split-Path $PSScriptRoot -Parent),
    [string]$OutputDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) 'artifacts\candidate'),
    [switch]$SkipPublish
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
$publishedAlias = [IO.Path]::GetFullPath((Join-Path (Split-Path $PSScriptRoot -Parent) 'artifacts\financial-twin-arm64.apk'))
if([IO.Path]::GetFullPath($destination) -eq $publishedAlias){throw 'Use um OutputDirectory de staging; o build não pode sobrescrever o APK publicado.'}
Push-Location (Join-Path $projectPath 'android')
try {
    & .\gradlew.bat app:assembleRelease -PreactNativeArchitectures=arm64-v8a --max-workers=2 --console=plain --build-cache *> (Join-Path $outputPath 'android-build.log')
    if ($LASTEXITCODE -ne 0) { throw "Gradle falhou: $LASTEXITCODE. Consulte android-build.log." }
    $apk = Join-Path $projectPath 'android\app\build\outputs\apk\release\app-release.apk'
    if (!(Test-Path -LiteralPath $apk)) { throw 'O Gradle não produziu o APK esperado.' }
    Copy-Item -LiteralPath $apk -Destination $destination -Force
    if (!$SkipPublish) { & (Join-Path $PSScriptRoot "publish-release.ps1") -ApkPath $destination -BuildDirectory $projectPath }
    Get-FileHash -LiteralPath $destination -Algorithm SHA256
    Write-Output "APK pronto: $destination"
} finally { Pop-Location }
