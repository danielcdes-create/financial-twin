$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$environmentFile = Join-Path $projectRoot '.env'
$setupLine = [IO.File]::ReadLines($environmentFile) | Where-Object { $_ -match '^ACCOUNT_SETUP_TOKEN=' } | Select-Object -First 1
if (!$setupLine) { throw 'Execute backend/scripts/configure_account_security.py no servidor primeiro.' }
$setupCode = $setupLine.Substring('ACCOUNT_SETUP_TOKEN='.Length).Trim().Trim('"', "'")
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$codeForm = New-Object System.Windows.Forms.Form
$codeForm.Text = 'Financial Twin - primeiro acesso'
$codeForm.Size = New-Object System.Drawing.Size(620, 250)
$codeForm.StartPosition = 'CenterScreen'
$description = New-Object System.Windows.Forms.Label
$description.Text = 'Abra o app atualizado. Escolha seu usuário e sua própria senha. Copie o código abaixo no campo Código de primeiro acesso. Não compartilhe o código.'
$description.Location = New-Object System.Drawing.Point(20, 20)
$description.Size = New-Object System.Drawing.Size(565, 55)
$codeField = New-Object System.Windows.Forms.TextBox
$codeField.Text = $setupCode
$codeField.ReadOnly = $true
$codeField.Location = New-Object System.Drawing.Point(20, 80)
$codeField.Size = New-Object System.Drawing.Size(560, 30)
$hint = New-Object System.Windows.Forms.Label
$hint.Text = 'Este código só autoriza o cadastro inicial. Depois você usará a senha que escolher, ou a digital do aparelho.'
$hint.Location = New-Object System.Drawing.Point(20, 125)
$hint.Size = New-Object System.Drawing.Size(560, 55)
$codeForm.Controls.AddRange(@($description, $codeField, $hint))
[void]$codeForm.ShowDialog()
$codeForm.Dispose()
