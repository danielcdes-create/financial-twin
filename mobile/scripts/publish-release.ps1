param(
    [Parameter(Mandatory=$true)][string]$ApkPath,
    [Parameter(Mandatory=$true)][string]$BuildDirectory,
    [string]$DestinationPath=(Join-Path (Split-Path $PSScriptRoot -Parent) 'artifacts\financial-twin-arm64.apk'),
    [string]$ExpectedSignerSha256,
    [string]$BuildTools='36.0.0'
)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$resolvedApk=(Resolve-Path -LiteralPath $ApkPath).Path
$buildProject=(Resolve-Path -LiteralPath $BuildDirectory).Path
$destination=[IO.Path]::GetFullPath($DestinationPath)
if($resolvedApk -eq $destination){throw 'O candidato deve estar em staging, separado do APK publicado.'}
$artifactRoot=Join-Path $projectRoot 'mobile\artifacts'
$appConfigPath=Join-Path $projectRoot 'mobile\app.json'
$readme=Join-Path $projectRoot 'README.md'
$python=Join-Path $projectRoot '.venv\Scripts\python.exe'
$pointerScript=Join-Path $projectRoot 'scripts\release_pointer.py'
$sourceVerifier=Join-Path $projectRoot 'scripts\verify_apk_source.py'
$sdkRoot=if($env:ANDROID_HOME){$env:ANDROID_HOME}else{Join-Path $env:LOCALAPPDATA 'Android\Sdk'}
$aapt=Join-Path $sdkRoot "build-tools\$BuildTools\aapt.exe"
$apksigner=Join-Path $sdkRoot "build-tools\$BuildTools\apksigner.bat"
foreach($required in @($python,$pointerScript,$sourceVerifier,$aapt,$apksigner,$appConfigPath,$readme)){
    if(!(Test-Path -LiteralPath $required -PathType Leaf)){throw "Gate de publicação indisponível: $required"}
}
if(!$env:JAVA_HOME){$env:JAVA_HOME=Join-Path $env:ProgramFiles 'Android\Android Studio\jbr'}
if(!(Test-Path -LiteralPath (Join-Path $env:JAVA_HOME 'bin\java.exe'))){throw 'Java não encontrado para verificar a assinatura.'}

function Get-VerifiedSigner([string]$Path){
    $verification=& $apksigner verify --verbose --print-certs $Path 2>&1
    if($LASTEXITCODE -ne 0){throw 'A assinatura Android não é válida; nenhuma promoção foi realizada.'}
    $certificates=@($verification | Select-String '^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$')
    if($certificates.Count -ne 1){throw 'A publicação exige um certificado de assinatura inequívoco.'}
    return $certificates[0].Matches[0].Groups[1].Value.ToUpperInvariant()
}
function Get-CleanCommit {
    $commit=& git -C $projectRoot rev-parse HEAD
    if($LASTEXITCODE -ne 0){throw 'Publicação exige referência Git recuperável.'}
    $changes=& git -C $projectRoot status --porcelain --untracked-files=all
    if($LASTEXITCODE -ne 0 -or $changes){throw 'Publicação exige checkout Git limpo, incluindo arquivos não rastreados.'}
    return $commit.Trim()
}
function Install-ReleaseFile([string]$Source,[string]$Target){
    $parent=Split-Path $Target -Parent
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary=Join-Path $parent ('.'+[IO.Path]::GetFileName($Target)+'.'+[guid]::NewGuid().ToString('N')+'.publish.tmp')
    try {
        Copy-Item -LiteralPath $Source -Destination $temporary
        if((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash){throw 'Cópia de publicação divergente.'}
        [IO.File]::Move($temporary,$Target,$true)
    } finally {if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary}}
}

$sourceCommit=Get-CleanCommit
$signer=Get-VerifiedSigner $resolvedApk
$previousAliasHash=if(Test-Path -LiteralPath $destination -PathType Leaf){(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash}else{$null}
if($ExpectedSignerSha256){
    $ExpectedSignerSha256=($ExpectedSignerSha256 -replace '[:\s]','').ToUpperInvariant()
    if($ExpectedSignerSha256 -notmatch '^[0-9A-F]{64}$'){throw 'ExpectedSignerSha256 deve conter o SHA256 completo do certificado.'}
    if($signer -ne $ExpectedSignerSha256){throw 'O certificado do candidato diverge da referência esperada.'}
}
if($previousAliasHash){
    if($signer -ne (Get-VerifiedSigner $destination)){throw 'O certificado do candidato não permite atualizar o APK publicado.'}
}elseif(!$ExpectedSignerSha256){throw 'Sem APK anterior, informe ExpectedSignerSha256 para autorizar o certificado de distribuição.'}
$badging=& $aapt dump badging $resolvedApk
if($LASTEXITCODE -ne 0){throw 'Falha ao ler o manifesto Android.'}
$packageLine=@($badging | Select-String "^package: name='com.financialtwin.app' versionCode='(\d+)' versionName='([^']+)'")
if($packageLine.Count -ne 1){throw 'Pacote ou versão inválida.'}
$releaseInfo=@{version_code=[int]$packageLine[0].Matches[0].Groups[1].Value;version_name=$packageLine[0].Matches[0].Groups[2].Value;sha256=(Get-FileHash -LiteralPath $resolvedApk -Algorithm SHA256).Hash;size_bytes=(Get-Item -LiteralPath $resolvedApk).Length;published_at=[DateTime]::UtcNow.ToString('o');source_commit=$sourceCommit;signer_sha256=$signer}
if($previousAliasHash){
    $oldBadging=& $aapt dump badging $destination
    if($LASTEXITCODE -ne 0){throw 'Falha ao conferir a versão publicada.'}
    $oldPackage=@($oldBadging | Select-String "^package: name='com.financialtwin.app' versionCode='(\d+)' versionName='([^']+)'")
    if($oldPackage.Count -ne 1){throw 'O alias anterior não contém o pacote esperado.'}
    $oldCode=[int]$oldPackage[0].Matches[0].Groups[1].Value
    if($releaseInfo.version_code -lt $oldCode -or ($releaseInfo.version_code -eq $oldCode -and $releaseInfo.sha256 -ne $previousAliasHash)){throw 'A promoção exige versionCode crescente; a mesma versão só pode repetir o mesmo APK.'}
}
$config=(Get-Content -LiteralPath $appConfigPath -Raw | ConvertFrom-Json).expo
if($config.version -ne $releaseInfo.version_name -or $config.android.versionCode -ne $releaseInfo.version_code){throw 'APK e configuração do app divergem.'}
$readmeText=Get-Content -LiteralPath $readme -Raw
if([regex]::Matches($readmeText,'(?m)^Release privado atual:').Count -ne 1){throw 'README deve conter exatamente um ponteiro de release atual.'}
$readmeHash=(Get-FileHash -LiteralPath $readme -Algorithm SHA256).Hash
$configHash=(Get-FileHash -LiteralPath $appConfigPath -Algorithm SHA256).Hash
$versionedApk=Join-Path $artifactRoot ('financial-twin-'+$releaseInfo.version_name+'.apk')
if((Test-Path -LiteralPath $versionedApk) -and (Get-FileHash -LiteralPath $versionedApk -Algorithm SHA256).Hash -ne $releaseInfo.sha256){throw 'Já existe outro APK para esta versão; incremente a versão.'}
$manifestPath=[IO.Path]::ChangeExtension($destination,'.manifest.json')
$provenancePath=[IO.Path]::ChangeExtension($versionedApk,'.provenance.json')
$identity=[guid]::NewGuid().ToString('N')
$stage=Join-Path $artifactRoot ('.publish-stage-'+$identity)
$backupRoot=Join-Path $artifactRoot ('release-backups\'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')+'-'+$identity)
$snapshots=@();$promoted=[Collections.Generic.List[string]]::new()
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    $stagedApk=Join-Path $stage 'candidate.apk'
    $stagedManifest=Join-Path $stage 'candidate.manifest.json'
    $stagedReadme=Join-Path $stage 'README.md'
    $stagedProvenance=Join-Path $stage 'provenance.json'
    Copy-Item -LiteralPath $resolvedApk -Destination $stagedApk
    if((Get-FileHash -LiteralPath $stagedApk -Algorithm SHA256).Hash -ne $releaseInfo.sha256){throw 'O candidato mudou durante a verificação.'}
    & $python $sourceVerifier --apk $stagedApk --build-project $buildProject --root $projectRoot --output $stagedProvenance | Out-Null
    if($LASTEXITCODE -ne 0){throw 'A origem do APK não foi comprovada.'}
    $provenance=Get-Content -LiteralPath $stagedProvenance -Raw | ConvertFrom-Json
    if($provenance.clean_head -ne $true -or $provenance.source_commit -ne $sourceCommit -or $provenance.apk_sha256 -ne $releaseInfo.sha256 -or $provenance.size_bytes -ne $releaseInfo.size_bytes -or $provenance.version_name -ne $releaseInfo.version_name -or $provenance.version_code -ne $releaseInfo.version_code){throw 'A proveniência não corresponde ao candidato e ao commit verificados.'}
    $releaseInfo.provenance_sha256=(Get-FileHash -LiteralPath $stagedProvenance -Algorithm SHA256).Hash
    $releaseInfo | ConvertTo-Json | Set-Content -LiteralPath $stagedManifest -Encoding utf8NoBOM
    Copy-Item -LiteralPath $readme -Destination $stagedReadme
    & $python $pointerScript --manifest $stagedManifest --app-config $appConfigPath --readme $stagedReadme | Out-Null
    if($LASTEXITCODE -ne 0){throw 'O ponteiro de release em staging não foi validado.'}
    if((Get-CleanCommit) -ne $sourceCommit -or (Get-FileHash -LiteralPath $readme -Algorithm SHA256).Hash -ne $readmeHash -or (Get-FileHash -LiteralPath $appConfigPath -Algorithm SHA256).Hash -ne $configHash){throw 'As fontes ou o README mudaram durante a verificação.'}
    $aliasHash=if(Test-Path -LiteralPath $destination -PathType Leaf){(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash}else{$null}
    if($aliasHash -ne $previousAliasHash){throw 'O APK publicado mudou durante a verificação.'}
    New-Item -ItemType Directory -Path $backupRoot | Out-Null
    $files=@(@{source=$stagedApk;target=$versionedApk},@{source=$stagedProvenance;target=$provenancePath},@{source=$stagedApk;target=$destination},@{source=$stagedManifest;target=$manifestPath},@{source=$stagedReadme;target=$readme})
    $index=0
    foreach($file in $files){
        $existed=Test-Path -LiteralPath $file.target -PathType Leaf
        $backup=Join-Path $backupRoot ($index.ToString()+'-'+[IO.Path]::GetFileName($file.target));$index++
        if($existed){
            Copy-Item -LiteralPath $file.target -Destination $backup
            if((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $file.target -Algorithm SHA256).Hash){throw 'Backup de promoção divergente.'}
        }
        $snapshots+=@{target=$file.target;existed=$existed;backup=$backup}
    }
    foreach($file in $files){Install-ReleaseFile $file.source $file.target;$promoted.Add($file.target)}
    & $python $pointerScript --manifest $manifestPath --app-config $appConfigPath --readme $readme --check | Out-Null
    if($LASTEXITCODE -ne 0){throw 'A promoção não convergiu com o manifesto e o README.'}
    if((Get-FileHash -LiteralPath $versionedApk -Algorithm SHA256).Hash -ne $releaseInfo.sha256 -or (Get-FileHash -LiteralPath $provenancePath -Algorithm SHA256).Hash -ne $releaseInfo.provenance_sha256){throw 'Os artefatos versionados divergiram após a promoção.'}
    $releaseInfo | ConvertTo-Json
    Write-Output "Release promovido; referência anterior preservada em $backupRoot"
} catch {
    $failure=$_
    $rollbackErrors=@()
    for($index=$snapshots.Count-1;$index -ge 0;$index--){
        $snapshot=$snapshots[$index]
        if(!$promoted.Contains($snapshot.target)){continue}
        try {
            if($snapshot.existed){Install-ReleaseFile $snapshot.backup $snapshot.target}
            elseif(Test-Path -LiteralPath $snapshot.target){Remove-Item -LiteralPath $snapshot.target}
        }catch{$rollbackErrors+=$snapshot.target}
    }
    if($rollbackErrors.Count){throw "Promoção interrompida e rollback incompleto. Restaure os arquivos em $backupRoot. Alvos: $($rollbackErrors -join ', ')"}
    throw $failure
} finally {
    $resolvedStage=[IO.Path]::GetFullPath($stage)
    $resolvedArtifacts=[IO.Path]::GetFullPath($artifactRoot).TrimEnd('\')+'\'
    if($resolvedStage.StartsWith($resolvedArtifacts,[StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedStage)){
        Remove-Item -LiteralPath $resolvedStage -Recurse
    }
}
