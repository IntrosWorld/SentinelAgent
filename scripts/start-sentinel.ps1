param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))

function Get-OllamaStatus {
    try {
        return Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 -Method Get
    } catch { return $null }
}

try {
    Write-Host 'SentinelAgent - interview launcher' -ForegroundColor Cyan
    $package = Get-Content -LiteralPath (Join-Path $projectRoot 'desktop\package.json') -Raw | ConvertFrom-Json
    $portable = Join-Path $projectRoot ("desktop\release\Sentinel-Desktop-{0}-Windows.exe" -f $package.version)
    $unpacked = Join-Path $projectRoot 'desktop\release\win-unpacked\Sentinel Desktop.exe'
    $application = $null
    if (Test-Path -LiteralPath $portable -PathType Leaf) { $application = $portable }
    elseif (Test-Path -LiteralPath $unpacked -PathType Leaf) { $application = $unpacked }
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $application -and (-not $npm -or -not (Test-Path -LiteralPath (Join-Path $projectRoot 'desktop\node_modules\electron')))) {
        throw 'No desktop build found. Build the portable app, or install the development dependencies first. See README.'
    }

    $ollamaCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
    $candidates = @($env:SENTINEL_OLLAMA_EXE)
    if ($ollamaCommand) { $candidates += $ollamaCommand.Source }
    if ($env:LOCALAPPDATA) { $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
    if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'Ollama\ollama.exe' }
    $ollamaExe = $candidates | Where-Object {
        $_ -and [IO.Path]::IsPathRooted($_) -and
        [IO.Path]::GetFileName($_) -ieq 'ollama.exe' -and (Test-Path -LiteralPath $_ -PathType Leaf)
    } | Select-Object -First 1
    $status = Get-OllamaStatus
    Write-Host ("Application: {0}" -f $(if ($application) { $application } else { 'development: npm start' }))
    if ($CheckOnly) {
        Write-Host ("Ollama executable: {0}" -f $ollamaExe)
        Write-Host ("Ollama reachable: {0}" -f [bool]$status)
        if (-not $status -and -not $ollamaExe) { throw 'Install Ollama or set SENTINEL_OLLAMA_EXE to its absolute executable path.' }
        Write-Host 'Check passed. No processes were started.' -ForegroundColor Green
        exit 0
    }
    if (-not $status) {
        if (-not $ollamaExe) { throw 'Ollama was not found. Install it or set SENTINEL_OLLAMA_EXE to the full path of ollama.exe.' }
        Write-Host 'Starting local Ollama...'
        $previousHost = $env:OLLAMA_HOST
        try {
            $env:OLLAMA_HOST = '127.0.0.1:11434'
            Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden -WorkingDirectory ([IO.Path]::GetDirectoryName($ollamaExe)) | Out-Null
        } finally { $env:OLLAMA_HOST = $previousHost }
        for ($attempt = 0; $attempt -lt 45; $attempt++) {
            $status = Get-OllamaStatus
            if ($status) { break }
            Start-Sleep -Seconds 1
        }
        if (-not $status) { throw 'Ollama did not become ready on localhost:11434. Check whether another process is using the port.' }
    }
    Write-Host 'Ollama is ready on http://127.0.0.1:11434.' -ForegroundColor Green
    $modelNames = @($status.models | ForEach-Object { $_.name })
    if ($modelNames.Count) { Write-Host ('Installed models: ' + ($modelNames -join ', ')) }
    else { Write-Warning 'No models installed. Documents still work; install a model through Ollama before model-based tasks.' }
    if ($application) {
        Write-Host 'Opening Sentinel. First portable extraction can take a minute or more.'
        Start-Process -FilePath $application -WorkingDirectory $projectRoot -WindowStyle Normal | Out-Null
    } else {
        Write-Host 'Starting development app. Keep this terminal open.'
        Push-Location (Join-Path $projectRoot 'desktop')
        try { & $npm.Source start; if ($LASTEXITCODE -ne 0) { throw 'Desktop development startup failed.' } }
        finally { Pop-Location }
    }
    Write-Host 'Ready. Choose your saved model profile in Models & setup. Ollama stays running after Sentinel closes.'
} catch {
    Write-Host ("Error: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
