$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
Push-Location $projectRoot
try {
    python -m PyInstaller --noconfirm --clean --onedir --name sentinel-runtime --distpath desktop/runtime --workpath build/desktop-runtime --specpath build --paths $projectRoot --hidden-import pywinauto --hidden-import psutil --hidden-import PIL.ImageGrab --collect-submodules comtypes --hidden-import docx --hidden-import openpyxl --collect-data docx --hidden-import reportlab.platypus --collect-data reportlab --hidden-import pypdf --exclude-module numpy --exclude-module torch --exclude-module ultralytics --exclude-module cv2 --exclude-module matplotlib --exclude-module scipy --exclude-module pandas --exclude-module freetype --exclude-module pytest --exclude-module IPython desktop/scripts/runtime_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime packaging failed' }
} finally { Pop-Location }
