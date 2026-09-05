$ErrorActionPreference = 'Stop'
python -m pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --icon app.ico --add-data "app.ico;." --name OneClickLauncher launcher.py
Write-Host "Built dist\OneClickLauncher.exe"
