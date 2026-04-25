param(
    [int]$Port = 8501,
    [switch]$InstallDeps,
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

if ([string]::IsNullOrWhiteSpace($DataDir)) {
    $DataDir = Join-Path $RepoRoot ".local_data"
}
$DataDir = [System.IO.Path]::GetFullPath($DataDir)
$AttachmentDir = Join-Path $DataDir "img_assets"
$DataFile = Join-Path $DataDir "tracker_data_web_v20.json"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
New-Item -ItemType Directory -Force -Path $AttachmentDir | Out-Null

if ($InstallDeps) {
    & python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} else {
    & python -c "import streamlit" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Missing Streamlit dependency. Run: .\run_local.bat -InstallDeps" -ForegroundColor Yellow
        exit 1
    }
}

$env:INART_STORAGE_BACKEND = "local"
$env:INART_DATA_FILE = $DataFile
$env:INART_ATTACHMENT_DIR = $AttachmentDir
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = "false"
$env:STREAMLIT_SERVER_HEADLESS = "false"

Write-Host "Starting INART PM local app..." -ForegroundColor Cyan
Write-Host "URL: http://localhost:$Port"
Write-Host "Data: $DataFile"
Write-Host "Attachments: $AttachmentDir"

& python -m streamlit run app.py `
    --server.address localhost `
    --server.port $Port `
    --server.fileWatcherType none `
    --browser.gatherUsageStats false
