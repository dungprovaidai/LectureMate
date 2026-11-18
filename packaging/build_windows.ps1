# Build bản portable LectureMate cho Windows x64 (P5.1.A.2).
# Chạy trong PowerShell tại thư mục gốc dự án:
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 [-WithModel base]
param(
    [string]$Python = "python",
    [string]$WithModel = ""      # tiny|base|small: đóng gói sẵn model ASR (cần mạng lúc build)
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== 1. Môi trường ảo build ==" -ForegroundColor Cyan
if (-not (Test-Path ".venv-build")) { Invoke-Expression "$Python -m venv .venv-build" }
& .\.venv-build\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-build\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv-build\Scripts\python.exe -m pip install -e .

Write-Host "== 2. Chạy test trước khi đóng gói ==" -ForegroundColor Cyan
$env:QT_QPA_PLATFORM = "offscreen"
& .\.venv-build\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Test lỗi — dừng build." }
Remove-Item Env:QT_QPA_PLATFORM

Write-Host "== 3. PyInstaller ==" -ForegroundColor Cyan
& .\.venv-build\Scripts\pyinstaller.exe --noconfirm --clean --distpath LectureMate-build --workpath build packaging\lecturemate.spec

$App = Join-Path $Root "LectureMate-build\LectureMate"
Copy-Item packaging\README-Windows.md $App
Copy-Item .env.example (Join-Path $App "config-mau.env.txt")
New-Item -ItemType Directory -Force (Join-Path $App "models") | Out-Null

if ($WithModel) {
    Write-Host "== 4. Tải sẵn model ASR '$WithModel' ==" -ForegroundColor Cyan
    $code = "from faster_whisper import WhisperModel; WhisperModel('$WithModel', device='cpu', compute_type='int8', download_root=r'$App\models')"
    & .\.venv-build\Scripts\python.exe -c $code
}

Write-Host "== 5. Kiểm tra: không đóng gói API key / .env ==" -ForegroundColor Cyan
$leak = Get-ChildItem -Recurse $App -Include ".env","*.env" -ErrorAction SilentlyContinue
if ($leak) { throw "Phát hiện file .env trong bản build: $leak" }

Write-Host "== 6. Smoke test exe ==" -ForegroundColor Cyan
& (Join-Path $App "LectureMate.exe") --smoke
Start-Sleep -Seconds 4

$zip = Join-Path $Root "LectureMate-build\LectureMate-portable-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $App -DestinationPath $zip
Write-Host "Xong: $zip" -ForegroundColor Green
Write-Host "Hãy thử trên một máy Windows sạch KHÔNG cài Python (xem README-Windows.md)."
