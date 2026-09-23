# prepare-tools.ps1 — 下载任务智能体工具链到 resources/tools/
#
# 用法：.\scripts\prepare-tools.ps1
# 功能：从 GitHub Releases 下载 uv / ripgrep / ruff / pandoc / python-build-standalone
#       解压到 resources/tools/ 目录，供 electron-builder 打包
#
# 注意：首次运行需要网络，下载约 ~120MB

param(
    [switch]$Force  # 强制重新下载（删除已有目录）
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsDir = Join-Path (Join-Path $projectRoot "resources") "tools"

Write-Host "=== InkCube 工具链准备 ===" -ForegroundColor Cyan
Write-Host "项目根目录: $projectRoot"
Write-Host "工具目录:   $toolsDir"
Write-Host ""

# ─── 版本配置 ───────────────────────────────────────────────
$tools = @{
    uv = @{
        version   = "0.11.7"
        url       = "https://github.com/astral-sh/uv/releases/download/0.11.7/uv-x86_64-pc-windows-msvc.zip"
        archive   = "uv.zip"
        type      = "zip"
        # zip 内有一级目录 uv-x86_64-pc-windows-msvc/，内含 uv.exe
        extractSubdir = "uv-x86_64-pc-windows-msvc"
        files     = @("uv.exe")
    }
    rg = @{
        version   = "14.1.1"
        url       = "https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-pc-windows-msvc.zip"
        archive   = "rg.zip"
        type      = "zip"
        extractSubdir = "ripgrep-14.1.1-x86_64-pc-windows-msvc"
        files     = @("rg.exe")
    }
    ruff = @{
        version   = "0.12.0"
        url       = "https://github.com/astral-sh/ruff/releases/download/0.12.0/ruff-x86_64-pc-windows-msvc.zip"
        archive   = "ruff.zip"
        type      = "zip"
        extractSubdir = "ruff-x86_64-pc-windows-msvc"
        files     = @("ruff.exe")
    }
    pandoc = @{
        version   = "3.6.4"
        url       = "https://github.com/jgm/pandoc/releases/download/3.6.4/pandoc-3.6.4-windows-x86_64.zip"
        archive   = "pandoc.zip"
        type      = "zip"
        extractSubdir = "pandoc-3.6.4"
        files     = @("pandoc.exe")
    }
}

# python-build-standalone 单独处理（tar.gz，目录结构不同）
$pythonStandalone = @{
    version   = "3.12.13+20250617"
    url       = "https://github.com/indygreg/python-build-standalone/releases/download/20250617/cpython-3.12.13+20250617-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    archive   = "python-build-standalone.tar.gz"
    type      = "tar.gz"
    targetDir = "python-build-standalone"
}

# ─── 检查是否所有工具已就位 ─────────────────────────────────
$allToolsReady = $true
if (-not $Force) {
    foreach ($name in $tools.Keys) {
        foreach ($file in $tools[$name].files) {
            $dst = Join-Path $toolsDir $file
            if (-not (Test-Path $dst)) {
                $allToolsReady = $false
                break
            }
        }
        if (-not $allToolsReady) { break }
    }
    # 检查 python-build-standalone
    $pyDstDir = Join-Path $toolsDir $pythonStandalone.targetDir
    if (-not (Test-Path $pyDstDir)) {
        $allToolsReady = $false
    }
}

if ($allToolsReady -and -not $Force) {
    Write-Host "所有工具已就位，跳过下载（使用 -Force 强制重新下载）" -ForegroundColor Green
    exit 0
}

# ─── 清理 ───────────────────────────────────────────────────
if ($Force -and (Test-Path $toolsDir)) {
    Write-Host "清理已有工具目录..." -ForegroundColor Yellow
    Remove-Item $toolsDir -Recurse -Force
}

# ─── 临时下载目录（保留已有归档缓存） ──────────────────────
$tempDir = Join-Path (Join-Path $projectRoot "resources") ".tools-temp"
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

# ─── 下载函数 ───────────────────────────────────────────────
function Download-File {
    param([string]$Url, [string]$OutFile)
    if (Test-Path $OutFile) {
        Write-Host "  已存在，跳过下载" -ForegroundColor DarkGray
        return
    }
    Write-Host "  下载: $Url"
    $ProgressPreference = 'SilentlyContinue'  # 加速下载
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
    } catch {
        Write-Host "  下载失败: $_" -ForegroundColor Red
        throw
    }
    $ProgressPreference = 'Continue'
    $fileSize = [math]::Round((Get-Item $OutFile).Length / 1MB, 1)
    Write-Host "  完成 $($fileSize) MB" -ForegroundColor Green
}

# ─── 下载各工具 ─────────────────────────────────────────────
foreach ($name in $tools.Keys) {
    $tool = $tools[$name]
    Write-Host "`n--- $name v$($tool.version) ---" -ForegroundColor Cyan

    $archivePath = Join-Path $tempDir $tool.archive
    Download-File -Url $tool.url -OutFile $archivePath

    # 解压
    $extractDir = Join-Path $tempDir "$name-extract"
    if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }

    if ($tool.type -eq "zip") {
        Expand-Archive -Path $archivePath -DestinationPath $extractDir -Force
    }

    # 复制到 tools 目录
    $srcDir = if ($tool.extractSubdir) {
        Join-Path $extractDir $tool.extractSubdir
    } else {
        $extractDir
    }

    foreach ($file in $tool.files) {
        $src = Join-Path $srcDir $file
        $dst = Join-Path $toolsDir $file
        if (Test-Path $src) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
            Copy-Item $src $dst -Force
            Write-Host "  已放置: $file" -ForegroundColor Green
        } else {
            Write-Host "  警告: 未找到 $file (预期路径: $src)" -ForegroundColor Yellow
        }
    }
}

# ─── python-build-standalone ────────────────────────────────
Write-Host "`n--- python-build-standalone v$($pythonStandalone.version) ---" -ForegroundColor Cyan

$pyArchive = Join-Path $tempDir $pythonStandalone.archive
Download-File -Url $pythonStandalone.url -OutFile $pyArchive

$pyExtractDir = Join-Path $tempDir "python-extract"
if (Test-Path $pyExtractDir) { Remove-Item $pyExtractDir -Recurse -Force }

if ($pythonStandalone.type -eq "tar.gz") {
    # 使用 tar 命令解压 .tar.gz（PowerShell 5+ 内置 tar 支持）
    Write-Host "  解压 tar.gz..."
    tar -xzf $pyArchive -C $tempDir
    # 解压后的目录名通常是 python/
    $extractedPython = Join-Path $tempDir "python"
    if (Test-Path $extractedPython) {
        $dstDir = Join-Path $toolsDir $pythonStandalone.targetDir
        if (Test-Path $dstDir) { Remove-Item $dstDir -Recurse -Force }
        Move-Item $extractedPython $dstDir -Force
        Write-Host "  已放置: python-build-standalone/" -ForegroundColor Green
    } else {
        Write-Host "  警告: 未找到解压的 python 目录" -ForegroundColor Yellow
    }
}

# ─── 生成版本信息 ───────────────────────────────────────────
$versions = @{
    uv      = $tools.uv.version
    rg      = $tools.rg.version
    ruff    = $tools.ruff.version
    pandoc  = $tools.pandoc.version
    python  = $pythonStandalone.version
    prepared_at = (Get-Date -Format "o")
}
$versionsPath = Join-Path $toolsDir "tool-versions.json"
$versions | ConvertTo-Json -Depth 3 | Set-Content $versionsPath -Encoding UTF8
Write-Host "`n版本信息已写入: $versionsPath" -ForegroundColor Green

# ─── 结果汇总 ───────────────────────────────────────────────
Write-Host "`n=== 准备完成 ===" -ForegroundColor Cyan
Write-Host "工具目录内容:" -ForegroundColor White
Get-ChildItem $toolsDir -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($toolsDir.Length + 1)
    $sizeMB = [math]::Round($_.Length / 1MB, 1)
    Write-Host "  $rel - $sizeMB MB"
}
Write-Host ""
Write-Host "下一步: 运行 .\scripts\build-installer.ps1 构建安装包" -ForegroundColor Yellow
