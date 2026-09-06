# Open one source file in Notepad, size the window, screenshot it, close it.
# Makes the point on slide 2: the program is plain text you can open anywhere.
#
# NOTE: SetForegroundWindow alone is not enough here -- Windows refuses the
# focus change when another app owns the foreground, and CopyFromScreen then
# grabs whatever is actually on top of that rectangle. So the window is pushed
# HWND_TOPMOST and activated through the shell before the grab, and put back
# afterwards.
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W3 {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@

$TOPMOST   = [IntPtr](-1)
$NOTOPMOST = [IntPtr](-2)
$SHOWWINDOW = 0x0040

$out = Join-Path $PSScriptRoot "shots_raw"
New-Item -ItemType Directory -Force $out | Out-Null

$file = "H:\UNM_Lightsheet\UNMScope_Python\src\unmscope\hardware\camera.py"
$name = "09_camera_module"

Get-Process notepad -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

$p = Start-Process notepad.exe -ArgumentList $file -PassThru
Start-Sleep -Seconds 3
$p.Refresh()
$h = $p.MainWindowHandle
if ($h -eq [IntPtr]::Zero) {
  $h = (Get-Process notepad | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1).MainWindowHandle
}
if ($h -eq [IntPtr]::Zero) { "FAILED to find the Notepad window"; exit 1 }

[W3]::ShowWindow($h, 9) | Out-Null
[W3]::SetWindowPos($h, $TOPMOST, 60, 60, 820, 660, $SHOWWINDOW) | Out-Null
try { (New-Object -ComObject wscript.shell).AppActivate($p.Id) | Out-Null } catch {}
[W3]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 1500

$r = New-Object W3+RECT
[W3]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.R - $r.L; $ht = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, (New-Object System.Drawing.Size $w, $ht))
$bmp.Save((Join-Path $out ($name + ".png")), [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"$name  $w x $ht  at $($r.L),$($r.T)"

[W3]::SetWindowPos($h, $NOTOPMOST, $r.L, $r.T, $w, $ht, $SHOWWINDOW) | Out-Null
Get-Process notepad -ErrorAction SilentlyContinue | Stop-Process -Force
"done"
