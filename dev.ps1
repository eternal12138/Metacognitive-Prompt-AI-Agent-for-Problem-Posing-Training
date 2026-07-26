[CmdletBinding()]
param(
    [switch]$Browser,
    [switch]$SkipInstall,
    [switch]$CheckOnly,
    [ValidateRange(1024, 65535)]
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$RequirementsFile = Join-Path $ProjectDir "requirements.txt"
$RequirementsMarker = Join-Path $VenvDir ".requirements.sha256"
$EnvFile = Join-Path $ProjectDir ".env"

Set-Location -LiteralPath $ProjectDir

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function New-RandomHex {
    param([int]$ByteCount = 32)
    $Bytes = New-Object byte[] $ByteCount
    $Generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Generator.GetBytes($Bytes)
    }
    finally {
        $Generator.Dispose()
    }
    return -join ($Bytes | ForEach-Object { $_.ToString("x2") })
}

function Initialize-EnvironmentFile {
    if (Test-Path -LiteralPath $EnvFile) {
        return
    }

    Write-Step "First run: creating local .env configuration"
    $Secret = New-RandomHex
    $Content = @"
# Local configuration. This file is excluded by .gitignore.
APP_ENV=development
APP_DATA_DIR=
FLASK_SECRET_KEY=$Secret
SESSION_COOKIE_SECURE=0
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=
DEFAULT_USER_PASSWORD=

# Set a real key before using AI chat and evaluation.
ARK_API_KEY=
ARK_API_URL=https://ark.cn-beijing.volces.com/api/v3/chat/completions
CHAT_MODEL_NAME=doubao-seed-2-0-pro-260215
EVAL_MODEL_NAME=deepseek-v3-2-251201

# Semantic memory is optional.
EMBEDDING_API_KEY=
EMBEDDING_API_URL=https://api.siliconflow.cn/v1/embeddings
EMBEDDING_MODEL_NAME=BAAI/bge-m3
"@
    [System.IO.File]::WriteAllText(
        $EnvFile,
        $Content,
        (New-Object System.Text.UTF8Encoding($false))
    )
    Write-Warning "Created .env. Set ARK_API_KEY in that file to enable AI features."
}

function Import-DotEnv {
    foreach ($Line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
        $Trimmed = $Line.Trim()
        if (-not $Trimmed -or $Trimmed.StartsWith("#")) {
            continue
        }

        $Separator = $Trimmed.IndexOf("=")
        if ($Separator -le 0) {
            continue
        }

        $Name = $Trimmed.Substring(0, $Separator).Trim()
        $Value = $Trimmed.Substring($Separator + 1).Trim()
        if ($Name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
            continue
        }
        if (
            $Value.Length -ge 2 -and
            (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or
             ($Value.StartsWith("'") -and $Value.EndsWith("'")))
        ) {
            $Value = $Value.Substring(1, $Value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Initialize-VirtualEnvironment {
    if (Test-Path -LiteralPath $PythonExe) {
        return
    }

    Write-Step "Creating Python virtual environment"
    $PyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if (-not $PyLauncher) {
        throw "Python was not found. Install Python 3.11 or 3.12 with Python Launcher enabled."
    }

    & $PyLauncher.Source -3 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonExe)) {
        throw "Virtual environment creation failed."
    }
}

function Install-DependenciesIfNeeded {
    if ($SkipInstall) {
        return
    }

    $CurrentHash = (Get-FileHash -LiteralPath $RequirementsFile -Algorithm SHA256).Hash
    $SavedHash = if (Test-Path -LiteralPath $RequirementsMarker) {
        (Get-Content -LiteralPath $RequirementsMarker -Raw).Trim()
    }
    else {
        ""
    }

    if ($CurrentHash -eq $SavedHash) {
        return
    }

    Write-Step "Installing project dependencies (the first run may take a few minutes)"
    & $PythonExe -m pip install -r $RequirementsFile
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed. Check the network connection and retry."
    }
    [System.IO.File]::WriteAllText(
        $RequirementsMarker,
        $CurrentHash,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

try {
    Initialize-EnvironmentFile
    Import-DotEnv
    Initialize-VirtualEnvironment
    Install-DependenciesIfNeeded

    $ArkKey = [Environment]::GetEnvironmentVariable("ARK_API_KEY", "Process")
    if ([string]::IsNullOrWhiteSpace($ArkKey)) {
        Write-Warning "ARK_API_KEY is not configured. The UI will open, but AI features are disabled."
        Write-Host "Edit this file: $EnvFile" -ForegroundColor Yellow
    }

    if ($CheckOnly) {
        Write-Step "Startup check passed"
        & $PythonExe --version
        Write-Host "Project: $ProjectDir"
        Write-Host "Config:  $EnvFile"
        exit 0
    }

    if ($Browser) {
        $Url = "http://127.0.0.1:$Port"
        Write-Step "Starting browser mode at $Url"
        Write-Host "Press Ctrl+C to stop the server." -ForegroundColor DarkGray
        $OpenBrowserJob = Start-Job -ScriptBlock {
            param($Address)
            Start-Sleep -Seconds 2
            Start-Process $Address
        } -ArgumentList $Url
        try {
            & $PythonExe -m flask --app app run --host 127.0.0.1 --port $Port
        }
        finally {
            Remove-Job -Job $OpenBrowserJob -Force -ErrorAction SilentlyContinue
        }
    }
    else {
        Write-Step "Starting desktop application"
        & $PythonExe app.py
    }
}
catch {
    Write-Host "`nStartup failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Run .\dev.ps1 -CheckOnly for diagnostics." -ForegroundColor Yellow
    exit 1
}

