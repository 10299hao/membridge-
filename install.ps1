param(
    [string]$InstallDir = (Join-Path $env:USERPROFILE '.membridge-app'),
    [string]$PythonPath = '',
    [switch]$SkipDependencies,
    [switch]$NoPath
)
$ErrorActionPreference = 'Stop'
function Get-MemBridgeHash([string]$LiteralPath) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash([IO.File]::ReadAllBytes($LiteralPath))).Replace('-', '') }
    finally { $algorithm.Dispose() }
}
$projectRoot = $PSScriptRoot
if (-not $PythonPath) {
    $projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $projectPython) { $PythonPath = $projectPython }
    else { $PythonPath = (Get-Command python.exe -ErrorAction Stop).Source }
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
if ($InstallDir -match '[%&|<>^]' -or $PythonPath -match '[%&|<>^]') {
    throw 'Choose a runtime/install path without shell metacharacters.'
}
if ((Test-Path -LiteralPath $InstallDir) -and ((Get-Item -LiteralPath $InstallDir).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'The installation directory must not be a symlink or junction.'
}
& $PythonPath -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
$manifestPath = Join-Path $InstallDir 'installation.json'
$binDir = Join-Path $InstallDir 'bin'
if ((Test-Path -LiteralPath $binDir) -and ((Get-Item -LiteralPath $binDir).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'The launcher directory must not be a symlink or junction.'
}
$runtimePython = $PythonPath
$ownsRuntime = $false
if (-not $SkipDependencies) {
    $runtimeDir = Join-Path $InstallDir 'runtime'
    if ((Test-Path -LiteralPath $runtimeDir) -and ((Get-Item -LiteralPath $runtimeDir).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The runtime directory must not be a symlink or junction.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $runtimeDir 'Scripts\python.exe'))) {
        & $PythonPath -m venv $runtimeDir
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the application runtime.' }
    }
    $runtimePython = Join-Path $runtimeDir 'Scripts\python.exe'
    & $runtimePython -m pip install -c (Join-Path $projectRoot 'requirements.lock') "$($projectRoot)[mcp]"
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed; existing user memory was preserved.' }
    $ownsRuntime = $true
}
New-Item -ItemType Directory -Path $binDir -Force | Out-Null
$cmdFile = Join-Path $binDir 'membridge.cmd'
$psFile = Join-Path $binDir 'membridge.ps1'
$cmdText = ('@echo off' + "`r`n" + '"{0}" -m membridge %*' + "`r`n" + 'exit /b %ERRORLEVEL%' + "`r`n") -f $runtimePython
$psText = ('& "{0}" -m membridge @args' + "`r`n" + 'exit $LASTEXITCODE' + "`r`n") -f $runtimePython
if (Test-Path -LiteralPath $manifestPath) {
    $previous = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($previous.installDir -ne $InstallDir) { throw 'Installation manifest does not match the chosen directory.' }
    foreach ($name in @('membridge.cmd', 'membridge.ps1')) {
        $target = Join-Path $binDir $name
        if ((Test-Path -LiteralPath $target) -and (Get-MemBridgeHash -LiteralPath $target) -ne $previous.files.$name) {
            throw "User-modified launcher was preserved: $target"
        }
    }
} else {
    foreach ($target in @($cmdFile, $psFile)) {
        if (Test-Path -LiteralPath $target) { throw "Existing launcher was preserved: $target" }
    }
}
[IO.File]::WriteAllText($cmdFile, $cmdText, [Text.Encoding]::ASCII)
[IO.File]::WriteAllText($psFile, $psText, [Text.UTF8Encoding]::new($false))
$pathAdded = $false
if (-not $NoPath) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($userPath -split ';' | Where-Object { $_ })
    if ($parts -notcontains $binDir) {
        [Environment]::SetEnvironmentVariable('Path', (($parts + $binDir) -join ';'), 'User')
        $pathAdded = $true
    }
    if (@($env:Path -split ';') -notcontains $binDir) { $env:Path = "$binDir;$env:Path" }
}
if ($previous -and $previous.pathAdded) { $pathAdded = $true }
$manifest = [ordered]@{ application='MemBridge'; installDir=$InstallDir; python=$runtimePython; ownsRuntime=$ownsRuntime;
    pathAdded=$pathAdded; files=@{ 'membridge.cmd'=(Get-MemBridgeHash -LiteralPath $cmdFile);
    'membridge.ps1'=(Get-MemBridgeHash -LiteralPath $psFile) } }
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
& $runtimePython -m membridge --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Installed runtime failed its startup check.' }
Write-Output "Installed MemBridge in $InstallDir"
Write-Output 'No environment activation is needed. Open a new terminal and run: membridge --help'
