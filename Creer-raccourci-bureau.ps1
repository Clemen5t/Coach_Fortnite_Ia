param(
    [Parameter(Mandatory=$true)]
    [string]$AppDir
)

$ErrorActionPreference = 'Stop'
$AppDir = $AppDir.TrimEnd('\')
$Desktop = [Environment]::GetFolderPath('Desktop')
$IconPath = Join-Path $AppDir 'CoachFortnite.ico'
$ShortcutPath = Join-Path $Desktop 'Coach Fortnite.lnk'
$VbsPath = Join-Path $AppDir 'CoachFortnite.vbs'

if (-not (Test-Path $VbsPath)) {
    throw "CoachFortnite.vbs est introuvable dans $AppDir"
}

Add-Type -AssemblyName System.Drawing

if (-not (Test-Path $IconPath)) {
    $bitmap = New-Object System.Drawing.Bitmap 256, 256
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    $background = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(16,24,39))
    $accent = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(52,211,153))
    $foreground = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(52,211,153), 12)

    $graphics.FillRectangle($background, 0, 0, 256, 256)
    $graphics.DrawRoundedRectangle = $null
    $graphics.DrawRectangle($pen, 18, 18, 220, 220)

    $font = New-Object System.Drawing.Font('Segoe UI', 86, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $format = New-Object System.Drawing.StringFormat
    $format.Alignment = [System.Drawing.StringAlignment]::Center
    $format.LineAlignment = [System.Drawing.StringAlignment]::Center
    $graphics.DrawString('CF', $font, $foreground, (New-Object System.Drawing.RectangleF(0,0,256,256)), $format)

    $handle = $bitmap.GetHicon()
    $icon = [System.Drawing.Icon]::FromHandle($handle)
    $stream = [System.IO.File]::Open($IconPath, [System.IO.FileMode]::Create)
    $icon.Save($stream)
    $stream.Close()

    $format.Dispose()
    $font.Dispose()
    $pen.Dispose()
    $foreground.Dispose()
    $accent.Dispose()
    $background.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
}

$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
$shortcut.Arguments = '"' + $VbsPath + '"'
$shortcut.WorkingDirectory = $AppDir
$shortcut.IconLocation = $IconPath + ',0'
$shortcut.Description = 'Coach Fortnite - IA locale'
$shortcut.Save()

Write-Host ''
Write-Host 'Raccourci cree sur le bureau : Coach Fortnite' -ForegroundColor Green
Write-Host 'Tu peux maintenant lancer le coach avec cette icone.'
