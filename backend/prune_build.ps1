# prune_build.ps1
# Aggressively removes unused and heavy files from the PyInstaller dist directory
# to minimize the final .exe installer size.

param (
    [string]$DistDir = ".\dist\main"
)

Write-Host "Starting aggressive pruning of $DistDir..."

if (-not (Test-Path $DistDir)) {
    Write-Error "Directory $DistDir does not exist."
    exit 1
}

$prunedSize = 0

# 1. Target specific heavy folders and binaries that we know we don't need
# For Aegis, we use llama-cpp-python, we don't need raw PyTorch CUDA DLLs,
# Triton, or heavy testing frameworks.
$heavyDirs = @(
    "torch\lib",          # PyTorch CUDA libraries
    "nvidia",             # Nvidia pip modules (cublas, cudnn)
    "triton",             # Triton compiler
    "xformers",
    "matplotlib\tests",
    "numpy\core\tests",
    "pandas\tests",
    "scipy\tests"
)

foreach ($dir in $heavyDirs) {
    $target = Join-Path $DistDir "_internal\$dir"
    if (Test-Path $target) {
        $size = (Get-ChildItem -Path $target -Recurse | Measure-Object -Property Length -Sum).Sum
        $prunedSize += $size
        Write-Host "Removing $target ($([math]::Round($size / 1MB, 2)) MB)"
        Remove-Item -Path $target -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# 2. Prune __pycache__ and unnecessary static files
$filesToRemove = @(
    "*.pyc",
    "*.pyo",
    "*.pyd.map",
    "*.pdb",             # Debug symbols
    "*.chm",
    "test_*.py"
)

foreach ($pattern in $filesToRemove) {
    $matched = Get-ChildItem -Path $DistDir -Filter $pattern -Recurse -ErrorAction SilentlyContinue
    foreach ($file in $matched) {
        $prunedSize += $file.Length
        Remove-Item -Path $file.FullName -Force -ErrorAction SilentlyContinue
    }
}

# 3. Clean up pytest cache
$pytestCache = Join-Path $DistDir "_internal\.pytest_cache"
if (Test-Path $pytestCache) {
    $size = (Get-ChildItem -Path $pytestCache -Recurse | Measure-Object -Property Length -Sum).Sum
    $prunedSize += $size
    Remove-Item -Path $pytestCache -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ("Pruning complete. Saved roughly " + [math]::Round($prunedSize / 1MB, 2) + " MB.")
