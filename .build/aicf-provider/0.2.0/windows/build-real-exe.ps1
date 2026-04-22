Write-Host "Building native worker executable with PyInstaller..."
py -m pip install pyinstaller
py -m PyInstaller --onefile --name aicf-provider-worker worker.py
Write-Host "Executable written to dist\\aicf-provider-worker.exe"
