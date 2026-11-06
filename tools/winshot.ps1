# Screenshot one window by its title (PrintWindow, works behind other windows):  winshot.ps1 "TSS Cam" out.png
param([string]$Title, [string]$Out)
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string c, string t);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  public struct RECT { public int L, T, R, B; }
}
"@
Add-Type -AssemblyName System.Drawing
[W]::SetProcessDPIAware() | Out-Null
$proc = Get-Process | Where-Object { $_.MainWindowTitle -eq $Title -and $_.ProcessName -ne "WindowsTerminal" } | Select-Object -First 1
$h = if ($proc) { [IntPtr]$proc.MainWindowHandle } else { [W]::FindWindow($null, $Title) }
if ($h -eq [IntPtr]::Zero) { Write-Error "no window titled $Title"; exit 1 }
$r = New-Object W+RECT
[W]::GetWindowRect($h, [ref]$r) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc()
[W]::PrintWindow($h, $dc, 2) | Out-Null   # 2 = PW_RENDERFULLCONTENT (WebView2 content too)
$g.ReleaseHdc($dc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"saved $Out ($($bmp.Width)x$($bmp.Height))"
