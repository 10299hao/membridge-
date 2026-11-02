$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskEnvFile = Join-Path $taskRoot '.env'
if (Test-Path -LiteralPath $taskEnvFile) {
    Write-Output '.env already exists; existing credentials were preserved.'
    exit 0
}
function New-RandomSecret {
    $taskBytes = New-Object byte[] 32
    $taskRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $taskRng.GetBytes($taskBytes) } finally { $taskRng.Dispose() }
    return [BitConverter]::ToString($taskBytes).Replace('-', '').ToLowerInvariant()
}
$taskPassword = New-RandomSecret
$taskApiKey = New-RandomSecret
@"
POSTGRES_PASSWORD=$taskPassword
MEMBRIDGE_API_KEY=$taskApiKey
MEMBRIDGE_LLM_URL=
MEMBRIDGE_LLM_KEY=
MEMBRIDGE_LLM_MODEL=
MEMBRIDGE_EMBEDDING_MODEL=
"@ | Set-Content -LiteralPath $taskEnvFile -Encoding ascii
Write-Output 'Created .env with random local credentials. Add model settings there if needed.'
