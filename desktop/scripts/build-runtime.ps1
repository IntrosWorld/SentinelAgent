$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
Push-Location $projectRoot
try {
    python -m PyInstaller --noconfirm --clean --onedir --name sentinel-runtime --distpath desktop/runtime --workpath build/desktop-runtime --specpath build --paths $projectRoot --hidden-import pywinauto --hidden-import psutil --hidden-import PIL.ImageGrab --collect-submodules comtypes desktop/scripts/runtime_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime packaging failed' }
} finally { Pop-Location }
