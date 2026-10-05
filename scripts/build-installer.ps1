# build-installer.ps1 — 一键构建 InkCube 安装包
#
# 用法：powershell -ExecutionPolicy Bypass -File .\scripts\build-installer.ps1
# 流程：
#   1. 准备工具链（若 resources/tools/ 不完整则调用 prepare-tools.ps1）
#   2. 装配 Python 后端（代码 + uv 安装依赖到 resources/python-backend/site-packages）
#   3. Vite 构建前端
#   4. electron-builder 生成安装包
#
# 后端打包方案 B（0.2 §9）：不使用 PyInstaller——RAG 重依赖（FlagEmbedding/torch、
# docling、rapidocr）为函数级延迟导入，PyInstaller 静态分析会丢失；安装包改为捆绑
# python-build-standalone 解释器 + site-packages 目录，运行时 spawn python main.py。

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "=== InkCube 安装包构建 ===" -ForegroundColor Cyan
Write-Host "项目根目录: $projectRoot"
Write-Host ""

# ─── Step 1: 准备工具链 ─────────────────────────────────────
$toolsDir = Join-Path (Join-Path $projectRoot "resources") "tools"
$runtimePython = Join-Path $toolsDir "python-build-standalone" "python.exe"
if (-not (Test-Path (Join-Path $toolsDir "uv.exe")) -or -not (Test-Path $runtimePython)) {
    Write-Host "`n[Step 1/5] 准备工具链..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "prepare-tools.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  工具链准备失败!" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "`n[Step 1/5] 工具链已存在，跳过" -ForegroundColor DarkGray
}

# ─── Step 2: 装配 Python 后端 ───────────────────────────────
Write-Host "`n[Step 2/5] 装配 Python 后端 (代码 + site-packages)..." -ForegroundColor Yellow

$backendDir = Join-Path (Join-Path $projectRoot "resources") "python-backend"

# 2a. 拷贝后端代码（排除 tests / __pycache__ / data 开发数据）
if (Test-Path $backendDir) { Remove-Item $backendDir -Recurse -Force }
robocopy (Join-Path $projectRoot "python") $backendDir /E /XD __pycache__ .pytest_cache tests data /XF *.pyc /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host "  后端代码拷贝失败 (robocopy exit $LASTEXITCODE)!" -ForegroundColor Red
    exit 1
}
Write-Host "  后端代码已拷贝到 resources/python-backend/" -ForegroundColor Green

# 2b. uv 安装依赖（干净 site-packages；--python 与运行时用同一解释器，消除版本断层）
# 镜像：pypi.org 直连超时、清华镜像 wheel 下载 403，阿里云镜像实测可用（2026-10）
$uvExe = Join-Path $toolsDir "uv.exe"
$sitePackages = Join-Path $backendDir "site-packages"
& $uvExe pip install --python $runtimePython --target $sitePackages --index-url https://mirrors.aliyun.com/pypi/simple/ -r (Join-Path $projectRoot "python" "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "  依赖安装失败!" -ForegroundColor Red
    exit 1
}
Write-Host "  依赖已安装到 resources/python-backend/site-packages/" -ForegroundColor Green

# ─── Step 3: Vite 构建前端 ───────────────────────────────────
Write-Host "`n[Step 3/5] Vite 构建前端..." -ForegroundColor Yellow
npm.cmd run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "  前端构建失败!" -ForegroundColor Red
    exit 1
}
Write-Host "  前端构建完成" -ForegroundColor Green

# ─── Step 4: electron-builder 生成安装包 ─────────────────────
Write-Host "`n[Step 4/5] electron-builder 生成安装包..." -ForegroundColor Yellow
npx.cmd electron-builder --win
if ($LASTEXITCODE -ne 0) {
    Write-Host "  electron-builder 失败!" -ForegroundColor Red
    exit 1
}

# ─── Step 5: 汇总 ────────────────────────────────────────────
Write-Host "`n[Step 5/5] 构建完成!" -ForegroundColor Cyan
$distDir = Join-Path $projectRoot "dist"
if (Test-Path $distDir) {
    Write-Host "`n安装包输出:" -ForegroundColor White
    Get-ChildItem $distDir -File | Where-Object { $_.Extension -in ".exe", ".msi", ".nsis" } | ForEach-Object {
        $sizeMB = [math]::Round($_.Length / 1MB, 1)
        Write-Host "  $($_.Name) - $sizeMB MB" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "完成!" -ForegroundColor Cyan
