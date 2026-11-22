# Apply StarHope-Portable.zip from the application folder without touching data.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Zip = Join-Path $Root 'StarHope-Portable.zip'
if (-not (Test-Path -LiteralPath $Zip)) {
    Write-Host ("[E_UPGRADE] StarHope-Portable.zip not found in " + $Root + ".")
    exit 1
}

if ($env:STARHOPE_UPGRADE_NO_STOP -ne '1') {
    $Python = Join-Path $Root 'runtime\win-x64\python\python.exe'
    if (Test-Path -LiteralPath $Python) {
        & $Python -B (Join-Path $Root 'scripts\upgrade_shutdown.py') $Root
        if ($LASTEXITCODE -ne 0) { exit 1 }
    }
}

$Lock = Join-Path $Root '.system_generated\runtime.lock'
if (Test-Path -LiteralPath $Lock) {
    try {
        $stream = [System.IO.File]::Open($Lock, 'Open', 'ReadWrite', 'None')
        $stream.Close()
    } catch {
        Write-Host '[E_UPGRADE] The service is still running. Close it from the web power button first.'
        exit 1
    }
}

$Sidecar = $Zip + '.sha256'
if (Test-Path -LiteralPath $Sidecar) {
    $expected = (Get-Content -LiteralPath $Sidecar -TotalCount 1).Split(' ')[0].ToLower()
    $actual = (Get-FileHash -LiteralPath $Zip -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) {
        Write-Host '[E_UPGRADE] Checksum mismatch; the archive was not applied.'
        exit 1
    }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Backup = Join-Path $Root ("data_backup\upgrade-" + $stamp)
New-Item -ItemType Directory -Path $Backup -Force | Out-Null
foreach ($name in @('starhope.db', 'starhope.db-wal', 'starhope.db-shm', '.env')) {
    $source = Join-Path $Root $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $Backup -Force
    }
}
foreach ($name in @('static\uploads', '.system_generated')) {
    $source = Join-Path $Root $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $Backup -Recurse -Force
    }
}

$Staging = Join-Path ([System.IO.Path]::GetTempPath()) ("starhope-upgrade-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Staging | Out-Null
try {
    Expand-Archive -LiteralPath $Zip -DestinationPath $Staging -Force
    if (-not (Test-Path -LiteralPath (Join-Path $Staging 'main.py')) -or
        -not (Test-Path -LiteralPath (Join-Path $Staging 'runtime'))) {
        Write-Host '[E_UPGRADE] The archive is not a complete StarHope package.'
        exit 1
    }
    # The running batch file and this updater cannot be replaced safely.
    foreach ($relative in @('StarHope.bat', 'StarHope.command', 'StarHope.sh',
                            'scripts\upgrade_portable.ps1', 'scripts\upgrade_portable.sh')) {
        Remove-Item -LiteralPath (Join-Path $Staging $relative) -Force -ErrorAction SilentlyContinue
    }
    Get-ChildItem -LiteralPath $Staging -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Root -Recurse -Force
    }
} catch {
    Write-Host ("[E_UPGRADE] Upgrade failed: " + $_.Exception.Message)
    exit 1
} finally {
    Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
}

Move-Item -LiteralPath $Zip -Destination (Join-Path $Backup 'applied.zip') -Force
if (Test-Path -LiteralPath $Sidecar) {
    Move-Item -LiteralPath $Sidecar -Destination (Join-Path $Backup 'applied.zip.sha256') -Force
}
Write-Host ("[ok] StarHope upgraded; local data was preserved. Backup: " + $Backup)
exit 0
