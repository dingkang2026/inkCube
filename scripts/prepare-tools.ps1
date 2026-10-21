# prepare-tools.ps1 - download and place the pinned tool chain into resources/tools/.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\prepare-tools.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\prepare-tools.ps1 -Force
#   powershell -ExecutionPolicy Bypass -File .\scripts\prepare-tools.ps1 -MirrorPrefix "https://<your-github-proxy>/https://github.com"
#
# Why this script exists: resources/tools/ is git-ignored (binaries are never
# committed), so a fresh clone has no tool chain and build-installer.ps1 Step 1
# aborts. This script recreates the exact pinned set from the public release
# assets -- Windows x86_64 targets only (matches the current installer target).
#
# Per-asset source order:
#   1. -MirrorPrefix + official URL  (optional accelerator proxy; the prefix is
#      prepended verbatim to the full GitHub URL, ghproxy-style)
#   2. Official GitHub Releases
#
# Verification: every tool is executed after download (--version) and its
# output must contain the pinned version; tool-versions.json is written only
# after all five tools pass, so the manifest never lies about what is on disk.
#
# The pinned versions below are the single source of truth. They mirror
# docs/0.2packaging-design.md section 6.2; update both together.
#
# ASCII-only on purpose: PowerShell 5.1 parses BOM-less files as ANSI, so CJK
# comments would become syntax errors (same constraint as build-installer.ps1).

param(
    [string]$MirrorPrefix = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is ~10x slower with the progress bar rendering
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsDir = Join-Path $projectRoot "resources\tools"
$pbsDir = Join-Path $toolsDir "python-build-standalone"

# --- pinned versions ----------------------------------------------------------
$UvVersion = "0.11.7"
$RgVersion = "14.1.1"
$RuffVersion = "0.12.0"
$PandocVersion = "3.6.4"
$PbsTag = "20251014"          # python-build-standalone release tag
$PbsPyVersion = "3.12.12"     # CPython build shipped by that tag

# -Filter matches exact file names, so "uv.exe" will not pick up "uvx.exe".
$ZipTools = @(
    @{ Name = "uv";     Repo = "astral-sh/uv";       Tag = $UvVersion;     Asset = "uv-$UvVersion-x86_64-pc-windows-msvc.zip";                 Exe = "uv.exe";     Expect = $UvVersion },
    @{ Name = "rg";     Repo = "BurntSushi/ripgrep"; Tag = $RgVersion;     Asset = "ripgrep-$RgVersion-x86_64-pc-windows-msvc.zip";             Exe = "rg.exe";     Expect = $RgVersion },
    @{ Name = "ruff";   Repo = "astral-sh/ruff";     Tag = $RuffVersion;   Asset = "ruff-$RuffVersion-x86_64-pc-windows-msvc.zip";              Exe = "ruff.exe";   Expect = $RuffVersion },
    @{ Name = "pandoc"; Repo = "jgm/pandoc";         Tag = $PandocVersion; Asset = "pandoc-$PandocVersion-windows-x86_64.zip";                  Exe = "pandoc.exe"; Expect = $PandocVersion }
)

function Test-ToolVersion {
    param([string]$ExePath, [string]$Expected)
    if (-not (Test-Path $ExePath)) { return $false }
    try {
        $out = (& $ExePath --version 2>&1 | Select-Object -First 1) -as [string]
    } catch { return $false }
    return ("$out".Contains($Expected))
}

function Write-Manifest {
    $m = [ordered]@{
        uv = $UvVersion
        rg = $RgVersion
        ruff = $RuffVersion
        pandoc = $PandocVersion
        python = $PbsPyVersion
        prepared_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
    }
    $json = $m | ConvertTo-Json
    # BOM-less UTF-8: Node's JSON.parse chokes on a BOM, and the app reads this
    # file for first-launch initialization (0.2 section 7.1).
    [System.IO.File]::WriteAllText((Join-Path $toolsDir "tool-versions.json"), $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "  tool-versions.json written" -ForegroundColor Green
}

function Get-Asset {
    param([string]$Url, [string]$OutFile)
    $urls = @()
    if ($script:MirrorPrefix) { $urls += ($script:MirrorPrefix.TrimEnd("/") + "/" + $Url) }
    $urls += $Url
    $last = $null
    foreach ($u in $urls) {
        try {
            Write-Host "  downloading: $u"
            Invoke-WebRequest -Uri $u -OutFile $OutFile -UseBasicParsing -TimeoutSec 600
            if ((Get-Item $OutFile).Length -lt 1MB) { throw "downloaded file is smaller than 1 MB - likely an error page, not the asset" }
            return
        } catch {
            $last = $_
            Write-Host "  source failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    throw @"
All download sources failed for:
  $Url
Last error: $last
Hints:
  - retry through an accelerator proxy:  -MirrorPrefix "https://<your-github-proxy>/https://github.com"
  - or download manually per docs/0.2packaging-design.md section 6.2 and place the files
"@
}

Write-Host "=== InkCube tool-chain preparation ===" -ForegroundColor Cyan
Write-Host "Target: $toolsDir"
New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null

# --- idempotent path: everything already on disk, just verify ------------------
$pyExePath = Join-Path $pbsDir "python.exe"
if (-not $Force) {
    $missing = @()
    foreach ($t in $ZipTools) {
        if (-not (Test-Path (Join-Path $toolsDir $t.Exe))) { $missing += $t.Exe }
    }
    if (-not (Test-Path $pyExePath)) { $missing += "python-build-standalone\python.exe" }

    if ($missing.Count -eq 0) {
        Write-Host "`nTool chain already present, verifying pinned versions..." -ForegroundColor Yellow
        $mismatch = @()
        foreach ($t in $ZipTools) {
            if (-not (Test-ToolVersion (Join-Path $toolsDir $t.Exe) $t.Expect)) { $mismatch += $t.Exe }
        }
        if (-not (Test-ToolVersion $pyExePath $PbsPyVersion)) { $mismatch += "python" }
        if ($mismatch.Count -gt 0) {
            throw "Existing tools do not match pinned versions: $($mismatch -join ', '). Re-run with -Force to re-download."
        }
        Write-Host "All tools match pinned versions." -ForegroundColor Green
        Write-Manifest
        exit 0
    }
    Write-Host "`nMissing files: $($missing -join ', ')" -ForegroundColor Yellow
}

# --- zip tools: download -> extract -> locate exe -> version check -> place ----
foreach ($t in $ZipTools) {
    Write-Host "`n[$($t.Name) $($t.Tag)]" -ForegroundColor Yellow
    $url = "https://github.com/$($t.Repo)/releases/download/$($t.Tag)/$($t.Asset)"
    $tmpZip = Join-Path $env:TEMP ("inkcube-" + $t.Asset)
    $tmpDir = Join-Path $env:TEMP ("inkcube-" + $t.Name + "-x")
    try {
        Get-Asset -Url $url -OutFile $tmpZip
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
        Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
        $exe = Get-ChildItem $tmpDir -Recurse -Filter $t.Exe | Select-Object -First 1
        if (-not $exe) { throw "$($t.Exe) not found inside $($t.Asset)" }
        $v = (& $exe.FullName --version 2>&1 | Select-Object -First 1) -as [string]
        if (-not ("$v".Contains($t.Expect))) {
            throw "$($t.Name) version mismatch: got '$v', expected output containing '$($t.Expect)'"
        }
        Copy-Item $exe.FullName (Join-Path $toolsDir $t.Exe) -Force
        Write-Host "  placed $($t.Exe)  (version OK: $v)" -ForegroundColor Green
    } finally {
        Remove-Item $tmpZip -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# --- python-build-standalone: tar.gz -> normalize layout -> version check ------
Write-Host "`n[python-build-standalone CPython $PbsPyVersion]" -ForegroundColor Yellow
$tarCmd = Get-Command tar.exe -ErrorAction SilentlyContinue
if (-not $tarCmd) { throw "tar.exe not found (bundled with Windows 10+); extract the tar.gz manually" }

$pbsAsset = "cpython-$PbsPyVersion+$PbsTag-x86_64-pc-windows-msvc-install_only.tar.gz"
$url = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsTag/$pbsAsset"
$tmpTgz = Join-Path $env:TEMP ("inkcube-" + $pbsAsset)
$tmpPbs = Join-Path $env:TEMP "inkcube-pbs-x"
try {
    Get-Asset -Url $url -OutFile $tmpTgz
    if (Test-Path $tmpPbs) { Remove-Item $tmpPbs -Recurse -Force }
    New-Item -ItemType Directory -Path $tmpPbs -Force | Out-Null
    & tar.exe -xzf $tmpTgz -C $tmpPbs
    if ($LASTEXITCODE -ne 0) { throw "tar extraction failed (exit $LASTEXITCODE)" }

    # install_only extracts its contents at the archive root; other variants
    # (install_only_stripped) nest everything under python/. Handle both.
    $src = $tmpPbs
    if (-not (Test-Path (Join-Path $src "python.exe"))) { $src = Join-Path $src "python" }
    if (-not (Test-Path (Join-Path $src "python.exe"))) { throw "python.exe not found inside $pbsAsset" }

    $v = (& (Join-Path $src "python.exe") --version 2>&1 | Select-Object -First 1) -as [string]
    if (-not ("$v".Contains($PbsPyVersion))) {
        throw "python version mismatch: got '$v', expected '$PbsPyVersion'"
    }

    if (Test-Path $pbsDir) { Remove-Item $pbsDir -Recurse -Force }
    Copy-Item $src $pbsDir -Recurse
    # verify the placed copy, not just the temp one
    $v2 = (& $pyExePath --version 2>&1 | Select-Object -First 1) -as [string]
    if (-not ("$v2".Contains($PbsPyVersion))) { throw "placed python.exe failed verification: '$v2'" }
    Write-Host "  placed python-build-standalone\  (version OK: $v2)" -ForegroundColor Green
} finally {
    Remove-Item $tmpTgz -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $tmpPbs -Recurse -Force -ErrorAction SilentlyContinue
}

# --- manifest -------------------------------------------------------------------
Write-Host "`nWriting manifest..." -ForegroundColor Yellow
Write-Manifest

Write-Host "`n=== Tool chain ready ===" -ForegroundColor Cyan
Get-ChildItem $toolsDir | ForEach-Object {
    if ($_.PSIsContainer) { Write-Host "  $($_.Name)\" } else { Write-Host ("  {0}  ({1} MB)" -f $_.Name, [math]::Round($_.Length / 1MB, 1)) }
}
Write-Host "`nNext: powershell scripts\build-installer.ps1" -ForegroundColor Cyan
