Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$proc = Get-Process -Id 48692 -ErrorAction SilentlyContinue
if (-not $proc) { 'process gone'; exit 1 }
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$h = $proc.MainWindowHandle
$null = [Win]::SetForegroundWindow($h)
Start-Sleep -Seconds 2
$r = New-Object Win+RECT
$null = [Win]::GetWindowRect($h, [ref]$r)
$w = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
"window rect: $($r.Left),$($r.Top) ${w}x${ht}"
if ($w -le 0 -or $ht -le 0) { 'bad rect'; exit 1 }
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save("$env:TEMP\desktop-shot.png", [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
'saved'
