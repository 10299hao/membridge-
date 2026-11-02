param([string]$InstallDir = (Join-Path $env:USERPROFILE '.membridge-app'), [switch]$NoPath)
$ErrorActionPreference = 'Stop'
function Get-MemBridgeHash([string]$LiteralPath) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash([IO.File]::ReadAllBytes($LiteralPath))).Replace('-', '') }
    finally { $algorithm.Dispose() }
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$manifestPath = Join-Path $InstallDir 'installation.json'
if (-not (Test-Path -LiteralPath $manifestPath)) { throw 'No MemBridge installation manifest exists at this path.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.application -ne 'MemBridge' -or $manifest.installDir -ne $InstallDir) { throw 'Installation identity mismatch.' }
if ((Get-Item -LiteralPath $InstallDir).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing a redirected install root.' }
$binDir = Join-Path $InstallDir 'bin'
if ((Get-Item -LiteralPath $binDir).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing redirected launchers.' }
$preserved = @()
foreach ($name in @('membridge.cmd', 'membridge.ps1')) {
    $target = Join-Path $binDir $name
    if (Test-Path -LiteralPath $target) {
        if ((Get-MemBridgeHash -LiteralPath $target) -eq $manifest.files.$name) {
            Remove-Item -LiteralPath $target -Force
        } else { $preserved += $target }
    }
}
if (-not $NoPath -and $manifest.pathAdded) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $remaining = @($userPath -split ';' | Where-Object { $_ -and $_ -ne $binDir })
    [Environment]::SetEnvironmentVariable('Path', ($remaining -join ';'), 'User')
}
if ($manifest.ownsRuntime -and $preserved.Count -eq 0) {
    $runtimeDir = [IO.Path]::GetFullPath((Join-Path $InstallDir 'runtime'))
    if (-not $runtimeDir.StartsWith($InstallDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Runtime path escapes the installation root.'
    }
    if (Test-Path -LiteralPath $runtimeDir) {
        if ((Get-Item -LiteralPath $runtimeDir).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing redirected runtime.' }
        Remove-Item -LiteralPath $runtimeDir -Recurse -Force
    }
}
if ($preserved.Count -eq 0) { Remove-Item -LiteralPath $manifestPath -Force }
Write-Output 'MemBridge launchers/runtime removed. Your projects and saved memory were preserved.'
if ($preserved.Count) { Write-Output ('User-modified launchers preserved: ' + ($preserved -join ', ')) }
