param(
    [string]$KeystorePath = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) '.release-keys\financial-twin-release.p12'),
    [string]$KeyAlias = 'financialtwin',
    [int]$ValidityDays = 10950,
    [switch]$GenerateRandomPassword
)
$ErrorActionPreference = 'Stop'
$javaHome = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { Join-Path $env:ProgramFiles 'Android\Android Studio\jbr' }
$keytool = Join-Path $javaHome 'bin\keytool.exe'
if (!(Test-Path -LiteralPath $keytool)) {
    throw "keytool.exe não encontrado em $keytool. Defina JAVA_HOME."
}
$resolvedTarget = [IO.Path]::GetFullPath($KeystorePath)
$repoRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd('\') + '\'
if ($resolvedTarget.StartsWith($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'O keystore privado deve ficar FORA do repositório Git (ex.: ../.release-keys/financial-twin-release.p12).'
}
if (Test-Path -LiteralPath $resolvedTarget) {
    throw "Keystore já existe em $resolvedTarget. Não sobrescreva uma chave de distribuição existente."
}
$parent = Split-Path $resolvedTarget -Parent
New-Item -ItemType Directory -Path $parent -Force | Out-Null

$currentPrincipal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$isDirectOwnerSession = $currentPrincipal -like "*\$($env:USERNAME)"

$passwordPlain = $null
$confirmPlain = $null
if ($GenerateRandomPassword) {
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $chars = New-Object char[] 48
    for ($i = 0; $i -lt 48; $i++) {
        $chars[$i] = $alphabet[$bytes[$i] % $alphabet.Length]
    }
    $passwordPlain = -join $chars
    [Array]::Clear($bytes, 0, $bytes.Length)
    [Array]::Clear($chars, 0, $chars.Length)
} else {
    $passwordPlain = Read-Host -Prompt 'Escolha a senha do keystore privado (mínimo 12 caracteres)' -MaskInput
    $confirmPlain = Read-Host -Prompt 'Repita a senha do keystore privado' -MaskInput
    if ($passwordPlain.Length -lt 12 -or $passwordPlain -cne $confirmPlain) {
        throw 'A senha deve ter ao menos 12 caracteres e coincidir com a confirmação.'
    }
}

$configPath = [IO.Path]::ChangeExtension($resolvedTarget, '.json')
try {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $prevJavaToolOpts = $env:JAVA_TOOL_OPTIONS
    $env:JAVA_TOOL_OPTIONS = "$prevJavaToolOpts -Duser.language=en -Duser.country=US".Trim()
    try {
        $genOut = & $keytool `
            -genkeypair `
            -v `
            -storetype PKCS12 `
            -keystore $resolvedTarget `
            -alias $KeyAlias `
            -keyalg RSA `
            -keysize 4096 `
            -validity $ValidityDays `
            -storepass $passwordPlain `
            -keypass $passwordPlain `
            -dname 'CN=Financial Twin Private Release, OU=Owner, O=Financial Twin, C=BR' 2>&1
        $genExit = $LASTEXITCODE
        $certInfo = & $keytool -list -v -storetype PKCS12 -keystore $resolvedTarget -alias $KeyAlias -storepass $passwordPlain 2>&1
        $listExit = $LASTEXITCODE
    } finally {
        $env:JAVA_TOOL_OPTIONS = $prevJavaToolOpts
    }
    $ErrorActionPreference = $prevEap

    if ($genExit -ne 0) { throw "Falha ao gerar keystore ($genExit): $($genOut -join ' ')" }
    if ($listExit -ne 0) { throw "Falha ao inspecionar certificado gerado ($listExit): $($certInfo -join ' ')" }

    $shaMatch = @($certInfo | ForEach-Object { "$_" } | Select-String 'SHA256:\s*([0-9A-Fa-f:]+)') | Select-Object -First 1
    $shaHex = if ($shaMatch) { ($shaMatch.Matches[0].Groups[1].Value -replace ':', '').ToUpperInvariant() } else { throw 'Não foi possível extrair o SHA-256 do certificado.' }

    $secretConfig = [ordered]@{
        store_file = $resolvedTarget
        key_alias = $KeyAlias
        store_password = $passwordPlain
        key_password = $passwordPlain
        signer_sha256 = $shaHex
        created_at = [DateTime]::UtcNow.ToString('o')
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($configPath, ($secretConfig | ConvertTo-Json), $utf8NoBom)

    if ($isDirectOwnerSession) {
        & icacls $parent /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" "NT AUTHORITY\SYSTEM:(OI)(CI)F" | Out-Null
        & icacls $resolvedTarget /inheritance:r /grant:r "$($env:USERNAME):(F)" "NT AUTHORITY\SYSTEM:(F)" | Out-Null
        & icacls $configPath /inheritance:r /grant:r "$($env:USERNAME):(F)" "NT AUTHORITY\SYSTEM:(F)" | Out-Null
    }

    Write-Host "Keystore privado criado com sucesso em: $resolvedTarget" -ForegroundColor Green
    Write-Host "Configuração local salva em: $configPath" -ForegroundColor Green
    Write-Host "SHA-256 do certificado (ExpectedSignerSha256): $shaHex" -ForegroundColor Cyan
} finally {
    $passwordPlain = $null
    $confirmPlain = $null
}
