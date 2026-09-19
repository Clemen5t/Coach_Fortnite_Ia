$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$dir=Join-Path $PSScriptRoot '..\build'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$path=Join-Path $dir 'Acolyte.ico'

$bmp=New-Object System.Drawing.Bitmap 256,256
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode=[System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::FromArgb(6,10,22))

$cyan=New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(34,211,238))
$purple=New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(124,58,237))
$white=New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
$pen=New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(168,85,247)),12

$shield=New-Object System.Drawing.Drawing2D.GraphicsPath
$shield.AddPolygon([System.Drawing.Point[]]@(
  (New-Object System.Drawing.Point 128,16),
  (New-Object System.Drawing.Point 226,58),
  (New-Object System.Drawing.Point 205,190),
  (New-Object System.Drawing.Point 128,238),
  (New-Object System.Drawing.Point 51,190),
  (New-Object System.Drawing.Point 30,58)
))
$g.FillPath($purple,$shield)
$g.DrawPath($pen,$shield)

$inner=New-Object System.Drawing.Drawing2D.GraphicsPath
$inner.AddPolygon([System.Drawing.Point[]]@(
  (New-Object System.Drawing.Point 128,45),
  (New-Object System.Drawing.Point 192,76),
  (New-Object System.Drawing.Point 177,166),
  (New-Object System.Drawing.Point 128,198),
  (New-Object System.Drawing.Point 79,166),
  (New-Object System.Drawing.Point 64,76)
))
$g.FillPath($cyan,$inner)

$font=[System.Drawing.Font]::new('Segoe UI',62,[System.Drawing.FontStyle]::Bold,[System.Drawing.GraphicsUnit]::Pixel)
$fmt=New-Object System.Drawing.StringFormat
$fmt.Alignment=[System.Drawing.StringAlignment]::Center
$fmt.LineAlignment=[System.Drawing.StringAlignment]::Center
$g.DrawString('AI',$font,$white,(New-Object System.Drawing.RectangleF 48,72,160,110),$fmt)

$hIcon=$bmp.GetHicon()
$icon=[System.Drawing.Icon]::FromHandle($hIcon)
$fs=[System.IO.File]::Open($path,[System.IO.FileMode]::Create)
$icon.Save($fs)
$fs.Dispose()

$fmt.Dispose();$font.Dispose();$pen.Dispose();$cyan.Dispose();$purple.Dispose();$white.Dispose()
$shield.Dispose();$inner.Dispose();$g.Dispose();$bmp.Dispose()
Write-Host "Icon generated: $path"
