# build-installer.ps1 — 一键构建 InkCube 安装包
#
# 用法：.\scripts\build-installer.ps1
# 流程：
#   1. 准备工具链（若 resources/tools/ 不存在则调用 prepare-tools.ps1）
#   2. PyInstaller 打包 Python 后端
#   3. 复制后端 exe 到 resources/python/
#   4. Vite 构建前端
#   5. electron-builder 生成安装包

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "=== InkCube 安装包构建 ===" -ForegroundColor Cyan
Write-Host "项目根目录: $projectRoot"
Write-Host ""

# ─── Step 1: 准备工具链 ─────────────────────────────────────
$toolsDir = Join-Path (Join-Path $projectRoot "resources") "tools"
if (-not (Test-Path (Join-Path $toolsDir "uv.exe"))) {
    Write-Host "`n[Step 1/5] 准备工具链..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "prepare-tools.ps1")
} else {
    Write-Host "`n[Step 1/5] 工具链已存在，跳过" -ForegroundColor DarkGray
}

# ─── Step 2: PyInstaller 打包后端 ────────────────────────────
Write-Host "`n[Step 2/5] PyInstaller 打包后端..." -ForegroundColor Yellow

# 检查 PyInstaller
$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    Write-Host "  安装 PyInstaller..." -ForegroundColor Yellow
    pip install pyinstaller
}

# 打包
pyinstaller inkcube-agent.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "  PyInstaller 打包失败!" -ForegroundColor Red
    exit 1
}

# 复制到 resources/python/
$pyResDir = Join-Path (Join-Path $projectRoot "resources") "python"
New-Item -ItemType Directory -Path $pyResDir -Force | Out-Null
Copy-Item (Join-Path $projectRoot "dist" "inkcube-agent.exe") $pyResDir -Force
Write-Host "  后端已复制到 resources/python/inkcube-agent.exe" -ForegroundColor Green

# ─── Step 3: Vite 构建前端 ───────────────────────────────────
Write-Host "`n[Step 3/5] Vite 构建前端..." -ForegroundColor Yellow
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "  前端构建失败!" -ForegroundColor Red
    exit 1
}
Write-Host "  前端构建完成" -ForegroundColor Green

# ─── Step 4: electron-builder 生成安装包 ─────────────────────
Write-Host "`n[Step 4/5] electron-builder 生成安装包..." -ForegroundColor Yellow
npx electron-builder --win
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
