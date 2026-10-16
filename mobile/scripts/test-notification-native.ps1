param([string]$OutputDirectory)
$ErrorActionPreference='Stop'
$cacheRoot=Join-Path $env:USERPROFILE '.gradle\caches\modules-2\files-2.1'
$javaPath='C:\Program Files\Android\Android Studio\jbr\bin\java.exe'
function FindJar([string]$relative){(Get-ChildItem -LiteralPath (Join-Path $cacheRoot $relative) -Filter '*.jar' -Recurse | Select-Object -First 1).FullName}
$compiler=@((FindJar 'org.jetbrains.kotlin\kotlin-compiler-embeddable\2.1.20'),(FindJar 'org.jetbrains.kotlin\kotlin-stdlib\2.1.20'),(FindJar 'org.jetbrains.kotlin\kotlin-script-runtime\2.1.20'),(FindJar 'org.jetbrains.kotlin\kotlin-reflect\1.6.10'),(FindJar 'org.jetbrains.kotlin\kotlin-daemon-embeddable\2.1.20'),(FindJar 'org.jetbrains.intellij.deps\trove4j\1.0.20200330'),(FindJar 'org.jetbrains.kotlinx\kotlinx-coroutines-core-jvm\1.8.0'),(FindJar 'org.jetbrains\annotations\13.0'))
$runtime=@($compiler[1],(FindJar 'org.json\json\20180813'))
$nativeDirectory=Join-Path $PSScriptRoot '..\modules\notification-capture\android\src\main\java\expo\modules\notificationcapture'
$sources=@((Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'fixtures\notification-inbox') -Filter '*.kt').FullName)+@((Join-Path $nativeDirectory 'NotificationCaptureService.kt'),(Join-Path $nativeDirectory 'NotificationInbox.kt'))
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
& $javaPath -cp ($compiler -join ';') org.jetbrains.kotlin.cli.jvm.K2JVMCompiler -no-stdlib -no-reflect -classpath ($runtime -join ';') -d $OutputDirectory @sources
if($LASTEXITCODE -ne 0){throw 'Notification Kotlin compile failed'}
& $javaPath -cp ((@($OutputDirectory)+$runtime) -join ';') InboxProbeKt
if($LASTEXITCODE -ne 0){throw 'Notification Kotlin probe failed'}
