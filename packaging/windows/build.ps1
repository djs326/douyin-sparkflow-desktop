# ============================================================
# DouYin SparkFlow 桌面版一键构建脚本
# 产物：dist\DouYinSparkFlow-Setup-<Version>.exe
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Version 1.0.1
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -AppOnly   # 只打包应用目录，不生成安装包
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
$AppDistDir = Join-Path $DistRoot "app"          # 应用目录（exe + _internal + chrome + node）
$SetupName  = "DouYinSparkFlow-Setup-$Version.exe"
$VenDir     = Join-Path $BuildRoot "venv"
$AppName    = "DouYinSparkFlow"
$PyInstallerSpec = Join-Path $BuildRoot "DouYinSparkFlow.spec"

Write-Host "== DouYin SparkFlow Desktop Build v$Version ==" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"

# ---------- 0. 前置检查 ----------
$PyCmd = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PyCmd) { throw "未找到 Python，请先安装 Python 3.9+ 并加入 PATH" }

# 查找 Inno Setup 编译器
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
    Write-Warning "未找到 Inno Setup 6（ISCC.exe）。将只产出应用目录，不生成安装包。"
    Write-Warning "下载安装：https://jrsoftware.org/isdl.php"
    $AppOnly = $true
}

# ---------- 1. 构建 venv 并安装依赖 ----------
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
if (-not (Test-Path (Join-Path $VenDir "Scripts\python.exe"))) {
    Write-Host "[1/6] 创建构建虚拟环境…" -ForegroundColor Yellow
    python -m venv $VenDir
} else {
    Write-Host "[1/6] 复用构建虚拟环境…" -ForegroundColor Yellow
}
$VenPy = Join-Path $VenDir "Scripts\python.exe"

Write-Host "[2/6] 安装依赖（清华 PyPI 镜像）…"
& $VenPy -m pip install --upgrade pip -q
& $VenPy -m pip install -r (Join-Path $SourceDir "requirements.txt") -r (Join-Path $SourceDir "requirements-web.txt") -r (Join-Path $SourceDir "requirements-build.txt") -i https://pypi.tuna.tsinghua.edu.cn/simple -q
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

# ---------- 2. Playwright Chromium ----------
Write-Host "[3/6] 下载 Playwright Chromium（首次约 170MB）…"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $BuildRoot "ms-playwright"
& $VenPy -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "Playwright 浏览器下载失败" }

# ---------- 3. 提取内置 Node（仅 node.exe，自包含） ----------
Write-Host "[4/6] 提取内置 Node 运行时…"
$NodeExe = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $NodeExe) { throw "未找到 node，请先安装 Node.js 18+ 并加入 PATH（协议发送功能需要）" }

# ---------- 4. PyInstaller 打包 ----------
Write-Host "[5/6] PyInstaller 打包 Python 应用…"
$AddData = @(
    "core\protocol_sender.mjs:core",
    "webui\templates:webui\templates",
    "webui\static:webui\static"
) -join ";"
& $VenPy -m PyInstaller `
    --noconfirm --clean `
    --onedir `
    --windowed `
    --name $AppName `
    --distpath $DistRoot `
    --workpath (Join-Path $BuildRoot "pyinstaller-work") `
    --specpath $BuildRoot `
    --add-data $AddData `
    --collect-all playwright `
    --collect-all webview `
    --collect-submodules uvicorn `
    --hidden-import websockets `
    --hidden-import httpx `
    --icon (Join-Path $PSScriptRoot "app.ico") `
    (Join-Path $SourceDir "launcher.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

# ---------- 5. 组装发布目录 ----------
Write-Host "[6/6] 组装发布目录…"
if (Test-Path $AppDistDir) { Remove-Item -Recurse -Force $AppDistDir }
$PyOut = Join-Path $DistRoot $AppName
Move-Item $PyOut $AppDistDir

# 复制 Playwright 浏览器 → chrome/
$ChromeDir = Join-Path $AppDistDir "chrome"
New-Item -ItemType Directory -Force -Path $ChromeDir | Out-Null
Get-ChildItem (Join-Path $env:PLAYWRIGHT_BROWSERS_PATH) -Directory | ForEach-Object {
    Copy-Item -Recurse -Force $_.FullName (Join-Path $ChromeDir $_.Name)
}

# 复制 node.exe → node/
$NodeDir = Join-Path $AppDistDir "node"
New-Item -ItemType Directory -Force -Path $NodeDir | Out-Null
Copy-Item -Force $NodeExe (Join-Path $NodeDir "node.exe")

Write-Host "发布目录：$AppDistDir"
Write-Host ("发布目录大小：{0:N1} MB" -f ((Get-ChildItem -Recurse $AppDistDir | Measure-Object Length -Sum).Sum / 1MB))

# ---------- 6. Inno Setup 安装包 ----------
if ($AppOnly) {
    Write-Host "已跳过安装包生成（-AppOnly）。" -ForegroundColor Yellow
    Write-Host "发布目录可用：$AppDistDir"
    exit 0
}

Write-Host "生成安装包（Inno Setup）…"
& $Iscc /DAppVersion=$Version /DAppSourceDir=$AppDistDir "/O$DistRoot" (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 编译失败" }

$SetupPath = Join-Path $DistRoot $SetupName
if (-not (Test-Path $SetupPath)) { throw "未找到安装包产物：$SetupPath" }
Write-Host "== 构建完成 ==" -ForegroundColor Green
Write-Host ("安装包：{0}（{1:N1} MB）" -f $SetupPath, ((Get-Item $SetupPath).Length / 1MB))
