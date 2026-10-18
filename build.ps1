#requires -Version 5.1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$version = (Select-String -Path "version.py" -Pattern '__version__ = "(.+)"').Matches.Groups[1].Value
Write-Host "Building NTE-GameAutoRun v$version ..."

$python = ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "未找到 .venv，请先运行 setup.bat"
}

& $python -m pip install -r requirements.txt pyinstaller --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

& ".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed `
    --name NTE-GameAutoRun --icon "assets\icon.ico" gui.py
if ($LASTEXITCODE -ne 0) { throw "GUI 打包失败" }

& ".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --console `
    --name NTE-TemplateCapture --icon "assets\icon.ico" make_template.py
if ($LASTEXITCODE -ne 0) { throw "模板采集工具打包失败" }

New-Item -ItemType Directory -Force "release" | Out-Null
$zip = "release\NTE-GameAutoRun-v$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path `
    "dist\NTE-GameAutoRun.exe", `
    "dist\NTE-TemplateCapture.exe", `
    "config.example.json", `
    "gui_settings.example.json", `
    "README.md", `
    "LICENSE", `
    "AI_DISCLOSURE.md" `
    -DestinationPath $zip

Write-Host "Done: $zip"
