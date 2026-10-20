# Builds Chessium.exe (PyInstaller) and the installer (Inno Setup 6).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = (Select-String -Path version.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Chessium $version"

python -m pip install -q -r requirements.txt pyinstaller
New-Item -ItemType Directory -Force build | Out-Null
python -m PyInstaller --noconfirm --clean chessium.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc "/DAppVersion=$version" installer\chessium.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Compress-Archive -Force -Path dist\Chessium\* -DestinationPath "dist\Chessium-$version-portable.zip"
Write-Host "Done: dist\Chessium-Setup-$version.exe, dist\Chessium-$version-portable.zip"
