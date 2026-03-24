param(
    [string]$PythonExe = "python",
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$entryPath = Join-Path $repoRoot "desktop_app\main.py"
$name = "InartPMDesktop"

$args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", $name,
    "--paths", $repoRoot,
    "--add-data", "$repoRoot\tracker_data_web_v20.json;."
)

if ($OneFile) {
    $args += "--onefile"
} else {
    $args += "--onedir"
}

$args += $entryPath

Push-Location $repoRoot
try {
    & $PythonExe @args
} finally {
    Pop-Location
}
