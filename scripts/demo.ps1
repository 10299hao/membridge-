$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Install the application first; see README.md.' }
$taskProject = Join-Path $taskRoot 'examples\demo-project'
New-Item -ItemType Directory -Path $taskProject -Force | Out-Null
$taskNames = @('MEMBRIDGE_HOME', 'MEMBRIDGE_SERVER', 'MEMBRIDGE_DATABASE_URL', 'MEMBRIDGE_LLM_URL', 'MEMBRIDGE_LLM_MODEL', 'MEMBRIDGE_EMBEDDING_MODEL', 'MEMBRIDGE_AI_URL', 'MEMBRIDGE_INGESTION_URL')
$taskPrevious = @{}
foreach ($taskName in $taskNames) {
    $taskPrevious[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
    [Environment]::SetEnvironmentVariable($taskName, $null, 'Process')
}
try {
    $env:MEMBRIDGE_HOME = Join-Path $taskRoot 'examples\.membridge'
    $env:MEMBRIDGE_SERVER = ''
    & $taskPython -m membridge save --from opencode --file (Join-Path $taskRoot 'data\samples\opencode\v2-session.json') --project $taskProject
    if ($LASTEXITCODE -ne 0) { throw 'Demo import failed' }
    & $taskPython -m membridge search 'Postgres' --project $taskProject
    if ($LASTEXITCODE -ne 0) { throw 'Demo search failed' }
    & $taskPython -m membridge handoff --to codex --project $taskProject
    if ($LASTEXITCODE -ne 0) { throw 'Demo handoff failed' }
    Write-Output "Open $taskProject\AGENTS.md to inspect the generated handoff."
} finally {
    foreach ($taskName in $taskNames) {
        [Environment]::SetEnvironmentVariable($taskName, $taskPrevious[$taskName], 'Process')
    }
}
