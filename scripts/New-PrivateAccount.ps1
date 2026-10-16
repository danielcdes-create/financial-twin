# Interactive local setup: the owner chooses the password, never the agent.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$projectRoot = Split-Path $PSScriptRoot -Parent
$environmentFile = Join-Path $projectRoot '.env'
$setupLine = [IO.File]::ReadLines($environmentFile) | Where-Object { $_ -match '^ACCOUNT_SETUP_TOKEN=' } | Select-Object -First 1
$setupCode = if ($setupLine) { $setupLine.Substring('ACCOUNT_SETUP_TOKEN='.Length).Trim().Trim('"', "'") } else { '' }
$apiBase = 'http://127.0.0.1:8000/v1/auth'
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Financial Twin - Primeiro acesso'
$form.ClientSize = New-Object System.Drawing.Size(550, 495)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$intro = New-Object System.Windows.Forms.Label
$intro.Text = 'Crie seu acesso privado. Escolha um usuário e uma senha que só você conhece. Depois, entre com eles no app e ative a digital, se quiser.'
$intro.Location = New-Object System.Drawing.Point(20, 20)
$intro.Size = New-Object System.Drawing.Size(510, 55)
$form.Controls.Add($intro)

function Add-Entry([string]$caption, [int]$top, [int]$length, [bool]$secret = $false) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $caption
    $label.Location = New-Object System.Drawing.Point(20, $top)
    $label.Size = New-Object System.Drawing.Size(510, 20)
    $field = New-Object System.Windows.Forms.TextBox
    $field.Location = New-Object System.Drawing.Point(20, ($top + 23))
    $field.Size = New-Object System.Drawing.Size(510, 25)
    $field.MaxLength = $length
    $field.UseSystemPasswordChar = $secret
    $form.Controls.AddRange(@($label, $field))
    return $field
}
$nameField = Add-Entry 'Como você quer ser chamado?' 85 80
$userField = Add-Entry 'Usuário (ex.: daniel; letras sem acento, números, ponto, hífen)' 145 64
$passwordField = Add-Entry 'Escolha sua senha (mínimo de 12 caracteres)' 205 256 $true
$confirmationField = Add-Entry 'Repita a senha' 265 256 $true
$message = New-Object System.Windows.Forms.Label
$message.Location = New-Object System.Drawing.Point(20, 320)
$message.Size = New-Object System.Drawing.Size(510, 45)
$create = New-Object System.Windows.Forms.Button
$create.Text = 'Criar minha conta'
$create.Location = New-Object System.Drawing.Point(20, 370)
$create.Size = New-Object System.Drawing.Size(510, 38)
$codeButton = New-Object System.Windows.Forms.Button
$codeButton.Text = 'Prefiro cadastrar pelo celular: ver código de primeiro acesso'
$codeButton.Location = New-Object System.Drawing.Point(20, 420)
$codeButton.Size = New-Object System.Drawing.Size(510, 35)
$privacy = New-Object System.Windows.Forms.Label
$privacy.Text = 'Sua senha vai somente para a API local. Ela não é registrada neste arquivo nem no chat.'
$privacy.Location = New-Object System.Drawing.Point(20, 465)
$privacy.Size = New-Object System.Drawing.Size(510, 25)
$form.Controls.AddRange(@($message, $create, $codeButton, $privacy))
$form.AcceptButton = $create
$codeButton.Add_Click({ & (Join-Path $PSScriptRoot 'Show-AccountSetupCode.ps1') })
$create.Add_Click({
    $requestBody = $null
    $reply = $null
    try {
        $user = $userField.Text.Trim().ToLowerInvariant()
        if (!$nameField.Text.Trim() -or $user -notmatch '^[a-z0-9][a-z0-9._-]{2,63}$') {
            $message.Text = 'Informe seu nome e um usuário válido com pelo menos 3 caracteres.'
            return
        }
        if ($passwordField.Text.Length -lt 12 -or $passwordField.Text -cne $confirmationField.Text) {
            $message.Text = 'Use uma senha com pelo menos 12 caracteres e repita a mesma senha.'
            return
        }
        if ($setupCode.Length -lt 32) { throw 'setup_unavailable' }
        $create.Enabled = $false
        $codeButton.Enabled = $false
        $message.Text = 'Criando seu acesso...'
        $form.Refresh()
        $requestBody = @{username=$user; display_name=$nameField.Text.Trim(); password=$passwordField.Text; setup_token=$setupCode; device_label='Primeiro acesso no computador'} | ConvertTo-Json -Compress
        # Never emit the body or the token returned by the API.
        $reply = Invoke-RestMethod -Uri "$apiBase/setup" -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($requestBody)) -TimeoutSec 20
        # The helper does not keep a session. Revoke its temporary signup session.
        try {
            $null = Invoke-RestMethod -Uri "$apiBase/logout" -Method Post -Headers @{Authorization="Bearer $($reply.access_token)"} -TimeoutSec 10
        } catch { }
        $message.Text = 'Conta criada! No app atualizado, toque em "Já configurei minha conta" e entre com seu usuário e senha.'
        $create.Text = 'Conta criada'
        $nameField.ReadOnly = $true
        $userField.ReadOnly = $true
        $passwordField.Enabled = $false
        $confirmationField.Enabled = $false
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 409) {
            $message.Text = 'Sua conta já foi criada. Abra o app atualizado e entre com seu usuário e senha.'
            $create.Enabled = $false
        } else {
            $message.Text = 'Não foi possível concluir. Confira o cadastro e se o aplicativo está aberto no computador. Tente novamente.'
            $create.Enabled = $true
            $codeButton.Enabled = $true
        }
    } finally {
        $passwordField.Clear()
        $confirmationField.Clear()
        $requestBody = $null
        $reply = $null
    }
})
$form.Add_FormClosed({ $passwordField.Clear(); $confirmationField.Clear() })
try { [void]$form.ShowDialog() }
finally { $setupCode = $null; $setupLine = $null; $form.Dispose() }
