# build-installer.ps1 - one-shot build script for the InkCube installer.
#
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\build-installer.ps1
# Flow:
#   1. Verify the tool chain (uv/rg/ruff/pandoc/python-build-standalone/tool-versions.json, abort on any miss)
#   2. Assemble the Python backend (code + deps installed by uv into resources/python-backend/site-packages)
#   3. Build the frontend with Vite
#   4. Produce the installer with electron-builder
#
# Packaging plan B (0.2 section 9): no PyInstaller. The RAG-heavy deps
# (FlagEmbedding/torch, docling, rapidocr) are imported lazily at function
# level, which PyInstaller's static analysis cannot see. The installer instead
# bundles the python-build-standalone interpreter plus a site-packages
# directory and spawns "python main.py" at runtime.
#
# This script is ASCII-only on purpose: PowerShell 5.1 parses BOM-less files
# as ANSI, and editors (PyCharm) keep stripping the BOM on rewrite, which
# turned CJK comments into syntax errors. Keep it ASCII.

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "=== InkCube installer build ===" -ForegroundColor Cyan
Write-Host "Project root: $projectRoot"
Write-Host ""

# --- Step 1: verify the tool chain ------------------------------------------
$toolsDir = Join-Path (Join-Path $projectRoot "resources") "tools"
$runtimePython = Join-Path (Join-Path $toolsDir "python-build-standalone") "python.exe"
Write-Host "`n[Step 1/5] Verifying tool chain..." -ForegroundColor Yellow

# prepare-tools.ps1 was removed (GitHub Releases is not reliably reachable
# from mainland China networks, so the tool chain is placed manually; the
# manifest and download URLs live in docs/0.2packaging-design.md section 6.2).
# The build verifies everything up front so missing pieces surface at build
# time instead of on an end-user machine mid-task.
$required = @("uv.exe", "rg.exe", "ruff.exe", "pandoc.exe", "tool-versions.json")
$missing = @($required | Where-Object { -not (Test-Path (Join-Path $toolsDir $_)) })
if (-not (Test-Path $runtimePython)) { $missing += "python-build-standalone\python.exe" }
if ($missing.Count -gt 0) {
    Write-Host "  Tool chain is missing the following files (resources/tools/):" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    Write-Host "  Download and place them manually, see docs/0.1dev-guide.md section 11.1" -ForegroundColor Yellow
    exit 1
}
Write-Host "  Tool chain complete" -ForegroundColor Green

# node-gyp (Step 4 rebuilds the better-sqlite3 native module) needs Python;
# honour the PYTHON env var first. Fail fast here instead of in Step 4 with
# "Could not find any Python installation to use". The python.exe under
# WindowsApps is the Microsoft Store execution-alias stub, not a real
# interpreter, so it does not count.
$pyCmd = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pyCmd -and $pyCmd.Source -like "*WindowsApps*") { $pyCmd = $null }
if (-not $env:PYTHON -and -not $pyCmd) {
    Write-Host "  Python not found: electron-builder needs it to rebuild better-sqlite3" -ForegroundColor Red
    Write-Host "  Set the env var first, e.g.: `$env:PYTHON = 'C:\Users\Kelvin\.conda\envs\inkcube\python.exe'" -ForegroundColor Yellow
    exit 1
}

# --- Step 2: assemble the Python backend ------------------------------------
Write-Host "`n[Step 2/5] Assembling Python backend (code + site-packages)..." -ForegroundColor Yellow

$backendDir = Join-Path (Join-Path $projectRoot "resources") "python-backend"

# 2a. Copy backend code (excluding tests / __pycache__ / dev data under data)
if (Test-Path $backendDir) { Remove-Item $backendDir -Recurse -Force }
robocopy (Join-Path $projectRoot "python") $backendDir /E /XD __pycache__ .pytest_cache tests data /XF *.pyc /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host "  Backend code copy failed (robocopy exit $LASTEXITCODE)!" -ForegroundColor Red
    exit 1
}
Write-Host "  Backend code copied to resources/python-backend/" -ForegroundColor Green

# 2b. Install deps with uv (clean site-packages; --python pins the same
# interpreter as runtime, removing any version drift).
# Mirror notes: pypi.org direct times out, Tsinghua mirror 403s on wheel
# downloads, Aliyun mirror verified working (2026-10).
$uvExe = Join-Path $toolsDir "uv.exe"
$sitePackages = Join-Path $backendDir "site-packages"
& $uvExe pip install --python $runtimePython --target $sitePackages --index-url https://mirrors.aliyun.com/pypi/simple/ -r (Join-Path (Join-Path $projectRoot "python") "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Dependency installation failed!" -ForegroundColor Red
    exit 1
}
Write-Host "  Dependencies installed into resources/python-backend/site-packages/" -ForegroundColor Green

# 2c. Generate sitecustomize.py: replicate what pywin32.pth does.
# In the packaged layout python-manager.js injects site-packages via
# PYTHONPATH, and Python does not process .pth files found on PYTHONPATH
# entries, so pywin32.pth never runs -> the pywintypes/pythoncom shims under
# win32\lib are not importable -> win32com crashes. site.py imports
# sitecustomize.py through the normal sys.path lookup (PYTHONPATH entries
# included) at interpreter startup, so we patch the pywin32 paths and the DLL
# directory there. See docs/0.2packaging-design.md sections 6.1 / 9.2.
$sitecustomize = Join-Path $sitePackages "sitecustomize.py"
$scContent = @'
# Auto-generated by scripts/build-installer.ps1 -- do not edit.
# The packaged layout injects site-packages via PYTHONPATH; Python ignores
# .pth files found on PYTHONPATH entries, so pywin32.pth never runs.
# site.py imports this module through normal sys.path lookup (PYTHONPATH
# entries included), which restores pywin32 importability.
# See docs/0.2packaging-design.md section 9.2.
import os
import sys

_sp = os.path.dirname(os.path.abspath(__file__))

# Paths normally appended by pywin32.pth
for _sub in ("win32", os.path.join("win32", "lib"), "pythonwin"):
    _p = os.path.join(_sp, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

# Register pywin32_system32 so the Windows loader finds pywintypes312.dll etc.
_dll_dir = os.path.join(_sp, "pywin32_system32")
if os.path.isdir(_dll_dir):
    os.add_dll_directory(_dll_dir)
    os.environ["PATH"] = _dll_dir + os.pathsep + os.environ.get("PATH", "")
'@
# Write BOM-less UTF-8 (the default encoding for Python 3 source files).
[System.IO.File]::WriteAllText($sitecustomize, $scContent, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  sitecustomize.py generated (pywin32 packaged-layout adaptation)" -ForegroundColor Green

# --- Step 3: build the frontend with Vite -----------------------------------
Write-Host "`n[Step 3/5] Building frontend with Vite..." -ForegroundColor Yellow
npm.cmd run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Frontend build failed!" -ForegroundColor Red
    exit 1
}
Write-Host "  Frontend build complete" -ForegroundColor Green

# --- Step 4: produce the installer with electron-builder --------------------
Write-Host "`n[Step 4/5] Producing installer with electron-builder..." -ForegroundColor Yellow
npx.cmd electron-builder --win
if ($LASTEXITCODE -ne 0) {
    Write-Host "  electron-builder failed!" -ForegroundColor Red
    exit 1
}

# --- Step 5: summary ----------------------------------------------------------
Write-Host "`n[Step 5/5] Build complete!" -ForegroundColor Cyan
$distDir = Join-Path $projectRoot "dist"
if (Test-Path $distDir) {
    Write-Host "`nInstaller output:" -ForegroundColor White
    Get-ChildItem $distDir -File | Where-Object { $_.Extension -in ".exe", ".msi", ".nsis" } | ForEach-Object {
        $sizeMB = [math]::Round($_.Length / 1MB, 1)
        Write-Host "  $($_.Name) - $sizeMB MB" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Done!" -ForegroundColor Cyan
