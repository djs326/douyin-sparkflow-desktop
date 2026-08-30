# ============================================================
# DouYin SparkFlow Desktop one-click build script
# Output: dist\DouYinSparkFlow-Setup-<Version>.exe
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Version 1.0.1
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -AppOnly
#     (-AppOnly: build the app directory only, skip the installer)
# NOTE: keep this file ASCII-only. Chinese comments break Windows PowerShell 5.1
#       which reads .ps1 without BOM as ANSI.
# ============================================================
param(
    [string]$Version = "1.0.0",
    [switch]$AppOnly
)

$ErrorActionPreference = "Stop"

$RepoRoot   = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$SourceDir  = Join-Path $RepoRoot "DouYinSparkFlow"
$BuildRoot  = Join-Path $RepoRoot "build"
$DistRoot   = Join-Path $RepoRoot "dist"
$AppDistDir = Join-Path $DistRoot "app"          # app dir (exe + _internal + chrome + node)
$SetupName  = "DouYinSparkFlow-Setup-$Version.exe"
$VenDir     = Join-Path $BuildRoot "venv"
$AppName    = "DouYinSparkFlow"
$PyWork     = Join-Path $BuildRoot "pyinstaller-work"

Write-Host "== DouYin SparkFlow Desktop Build v$Version ==" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"

# ---------- 0. prerequisites ----------
$PyCmd = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PyCmd) { throw "Python 3.9+ not found in PATH" }

$Iscc = $null
$isccCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
)
foreach ($c in $isccCandidates) {
    if (Test-Path $c) { $Iscc = $c; break }
}
if (-not $Iscc -and -not $AppOnly) {
    Write-Warning "Inno Setup 6 (ISCC.exe) not found. Building the app directory only."
    Write-Warning "Download: https://jrsoftware.org/isdl.php"
    $AppOnly = $true
}

# ---------- 1. build venv + deps ----------
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
if (-not (Test-Path (Join-Path $VenDir "Scripts\python.exe"))) {
    Write-Host "[1/5] Creating build venv..." -ForegroundColor Yellow
    python -m venv $VenDir
} else {
    Write-Host "[1/5] Reusing build venv..." -ForegroundColor Yellow
}
$VenPy = Join-Path $VenDir "Scripts\python.exe"

Write-Host "[2/5] Installing dependencies (Tsinghua PyPI mirror)..."
& $VenPy -m pip install --upgrade pip -q
if ($env:SPARKFLOW_BUILD_NO_MIRROR) {
    # CI (GitHub Actions) runs outside China: skip the Tsinghua mirror.
    Write-Host "SPARKFLOW_BUILD_NO_MIRROR is set; using default PyPI index"
    & $VenPy -m pip install -r (Join-Path $SourceDir "requirements.txt") -r (Join-Path $SourceDir "requirements-web.txt") -r (Join-Path $SourceDir "requirements-build.txt") -q
} else {
    & $VenPy -m pip install -r (Join-Path $SourceDir "requirements.txt") -r (Join-Path $SourceDir "requirements-web.txt") -r (Join-Path $SourceDir "requirements-build.txt") -i https://pypi.tuna.tsinghua.edu.cn/simple -q
}
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# ---------- 2. bundled Node (node.exe is self-contained) ----------
Write-Host "[3/5] Locating Node runtime..."
# IMPORTANT: PATH may contain node.cmd/.bat shims (e.g. DSH harness node.cmd
# placeholder, Microsoft Store WindowsApps stub). Copying a .cmd/.bat as
# node.exe yields an invalid executable ("not a valid application for this
# OS platform") in the packaged app. Enumerate all candidates, pick the first
# real .exe with non-zero size, and fall back to `where.exe` enumeration.
$NodeExe = $null
foreach ($candidate in (Get-Command node -ErrorAction SilentlyContinue -All | Select-Object -ExpandProperty Source)) {
    if ($candidate -match '\.exe$') {
        try {
            if ((Get-Item -LiteralPath $candidate).Length -gt 0) { $NodeExe = $candidate; break }
        } catch { }
    }
}
if (-not $NodeExe) {
    $NodeExe = ((where.exe node 2>$null) | Where-Object { $_ -match '\.exe$' } | Select-Object -First 1)
}
if (-not $NodeExe) { throw "Node.js 18+ not found in PATH (needed for the protocol sender)" }
Write-Host "Using node: $NodeExe"

# ---------- 3. PyInstaller (spec file avoids CLI arg pitfalls) ----------
Write-Host "[4/5] PyInstaller packaging..."
$LauncherPy  = Join-Path $SourceDir "launcher.py"
$IconPath    = Join-Path $PSScriptRoot "app.ico"
$SpecPath    = Join-Path $BuildRoot "DouYinSparkFlow.spec"

$spec = @"
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

launcher = r'$LauncherPy'
icon_path = r'$IconPath'

datas = [
    (r'$($SourceDir -replace '\\','/')/core/protocol_sender.mjs', 'core'),
    (r'$($SourceDir -replace '\\','/')/webui/templates', 'webui/templates'),
    (r'$($SourceDir -replace '\\','/')/webui/static', 'webui/static'),
    (r'$($PSScriptRoot -replace '\\','/')/app.ico', '.'),
]
binaries = []
hiddenimports = ['websockets', 'httpx']

for pkg in ('playwright', 'webview'):
    p_datas, p_binaries, p_hidden = collect_all(pkg)
    datas += p_datas
    binaries += p_binaries
    hiddenimports += p_hidden

hiddenimports += ['uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
                  'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
                  'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
                  'uvicorn.lifespan', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off',
                  'uvicorn.lifespan.auto', 'uvicorn.lifespan.types']

a = Analysis(
    [launcher],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='$AppName',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=icon_path,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='$AppName',
)
"@
Set-Content -Path $SpecPath -Value $spec -Encoding UTF8
& $VenPy -m PyInstaller $SpecPath --noconfirm --clean --distpath $DistRoot --workpath $PyWork
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# ---------- 5. assemble app dir ----------
Write-Host "[5/5] Assembling app directory..."
if (Test-Path $AppDistDir) { Remove-Item -Recurse -Force $AppDistDir }
$PyOut = Join-Path $DistRoot $AppName
if (-not (Test-Path $PyOut)) { throw "PyInstaller output not found: $PyOut (clean dist/ and retry)" }
Move-Item $PyOut $AppDistDir

# Browser uses the user's system Edge/Chrome (probed by system_browser_executable); no bundled Chromium.
$NodeDir = Join-Path $AppDistDir "node"
New-Item -ItemType Directory -Force -Path $NodeDir | Out-Null
Copy-Item -Force $NodeExe (Join-Path $NodeDir "node.exe")

Write-Host "App directory: $AppDistDir"
$sizeMb = [math]::Round(((Get-ChildItem -Recurse $AppDistDir | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "App directory size: $sizeMb MB"

# ---------- 6. Inno Setup installer ----------
if ($AppOnly) {
    Write-Host "Skipped installer (-AppOnly). Use: $AppDistDir"
    exit 0
}

Write-Host "Building installer (Inno Setup)..."
& $Iscc /DAppVersion=$Version /DAppSourceDir=$AppDistDir "/O$DistRoot" (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }

$SetupPath = Join-Path $DistRoot $SetupName
if (-not (Test-Path $SetupPath)) { throw "Installer not found: $SetupPath" }
$setupMb = [math]::Round(((Get-Item $SetupPath).Length / 1MB), 1)
Write-Host "== Build complete ==" -ForegroundColor Green
Write-Host "Installer: $SetupPath ($setupMb MB)"
