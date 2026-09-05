Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W2 {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
$out = Join-Path $PSScriptRoot "shots_raw"
New-Item -ItemType Directory -Force $out | Out-Null
$targets = @(
  @{ path = "H:\UNM_Lightsheet\UNMScope_Python\tests";  name = "06_tests" },
  @{ path = "H:\UNM_Lightsheet\UNMScope_Python\docs";   name = "07_docs" },
  @{ path = "H:\UNM_Lightsheet\UNMScope_Python\spikes"; name = "08_spikes" }
)
$shell = New-Object -ComObject Shell.Application
foreach ($t in $targets) {
  $shell.Windows() | ForEach-Object { try { $_.Quit() } catch {} }
  Start-Sleep -Milliseconds 700
  Start-Process explorer.exe -ArgumentList $t.path
  Start-Sleep -Seconds 3
  $win = $shell.Windows() | Where-Object { $_.LocationURL -ne "" } | Select-Object -First 1
  if ($null -eq $win) { "FAILED $($t.path)"; continue }
  $h = [IntPtr]$win.HWND
  [W2]::ShowWindow($h, 9) | Out-Null
  [W2]::SetWindowPos($h, [IntPtr]::Zero, 60, 20, 1000, 1010, 0x0040) | Out-Null
  [W2]::SetForegroundWindow($h) | Out-Null
  Start-Sleep -Milliseconds 900
  $r = New-Object W2+RECT
  [W2]::GetWindowRect($h, [ref]$r) | Out-Null
  $w = $r.R - $r.L; $ht = $r.B - $r.T
  $bmp = New-Object System.Drawing.Bitmap $w, $ht
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($r.L, $r.T, 0, 0, (New-Object System.Drawing.Size $w, $ht))
  $bmp.Save((Join-Path $out ($t.name + ".png")), [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  "$($t.name)  $w x $ht"
}
$shell.Windows() | ForEach-Object { try { $_.Quit() } catch {} }
"done"
