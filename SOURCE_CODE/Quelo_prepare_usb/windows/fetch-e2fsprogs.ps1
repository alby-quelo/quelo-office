# Scarica mke2fs.exe e DLL Cygwin per formattazione ext4 su Windows.
# Uso: powershell -ExecutionPolicy Bypass -File fetch-e2fsprogs.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dest = Join-Path $ScriptDir "tools\e2fsprogs"
$Url = "https://mirrors.kernel.org/sourceware/cygwin/x86_64/release/e2fsprogs/e2fsprogs-1.42.12-1.tar.xz"
$TmpXz = Join-Path $env:TEMP "quelo-e2fsprogs.tar.xz"
$TmpDir = Join-Path $env:TEMP "quelo-e2fsprogs-extract"

Write-Host "Scarico e2fsprogs (mke2fs) da mirror Cygwin..."
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

if (Test-Path $TmpDir) { Remove-Item -Recurse -Force $TmpDir }
New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null

try {
    Invoke-WebRequest -Uri $Url -OutFile $TmpXz -UseBasicParsing
} catch {
    Write-Error "Download fallito: $_"
    exit 1
}

$tar = Get-Command tar -ErrorAction SilentlyContinue
if (-not $tar) {
    Write-Error "Comando tar non trovato (serve Windows 10+ o Cygwin)."
    exit 1
}

& tar -xf $TmpXz -C $TmpDir
Remove-Item -Force $TmpXz -ErrorAction SilentlyContinue

$mke2fs = Get-ChildItem -Path $TmpDir -Recurse -Filter "mke2fs.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $mke2fs) {
    Write-Error "mke2fs.exe non trovato nell'archivio scaricato."
    exit 1
}

Copy-Item -Force $mke2fs.FullName (Join-Path $Dest "mke2fs.exe")

$binDirs = @(
    (Join-Path $TmpDir "usr\bin"),
    (Join-Path $TmpDir "usr\sbin"),
    (Split-Path $mke2fs.FullName -Parent)
)
$dllNames = @("cygwin1.dll", "cyggcc_s-seh-1.dll", "cygiconv-2.dll", "cygintl-8.dll", "cygpcre-1.dll")
foreach ($dll in $dllNames) {
    $found = $false
    foreach ($dir in $binDirs) {
        $src = Join-Path $dir $dll
        if (Test-Path $src) {
            Copy-Item -Force $src (Join-Path $Dest $dll)
            $found = $true
            break
        }
    }
    if (-not $found) {
        $alt = Get-ChildItem -Path $TmpDir -Recurse -Filter $dll -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($alt) {
            Copy-Item -Force $alt.FullName (Join-Path $Dest $dll)
        }
    }
}

Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue

if (-not (Test-Path (Join-Path $Dest "mke2fs.exe"))) {
    Write-Error "Installazione mke2fs non riuscita."
    exit 1
}

Write-Host "mke2fs installato in: $Dest"
exit 0
