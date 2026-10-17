<#
.SYNOPSIS
    Start the ACE Migration Factory locally.

.DESCRIPTION
    Prepares the virtual environment and launches the server on
    http://127.0.0.1:8080.

    Access is granted by signing in through the web console, which issues the
    bearer token itself after verifying a password. Nothing needs to be minted
    by hand, and no signing secret has to be distributed: when
    FACTORY_JWT_HS256_SECRET is not set, the server generates a key on first run
    and persists it beside the database as <database>.signing-key.

    Open the console and use "Create one" to register the first account. That
    first account also receives the administration roles, so it can grant
    approver roles to everyone who registers after it.

.PARAMETER Port
    Port to listen on. Defaults to 8080.

.PARAMETER Database
    SQLite database path. Defaults to factory-v2.db beside this script.

.PARAMETER BindAddress
    Interface to bind. Defaults to 127.0.0.1 so the governance API is not
    exposed on the LAN.

.PARAMETER WorkspaceRoot
    Root directory under which all pipeline workspaces are created.
    Overrides the FACTORY_WORKSPACE_ROOT environment variable.
    When neither is set the server picks an OS-appropriate user-data directory
    (e.g. %LOCALAPPDATA%\ACEMigrationFactory\workspaces on Windows) so no
    configuration is needed out of the box.
    For server deployments set FACTORY_WORKSPACE_ROOT in the service environment
    instead of passing this flag.

.PARAMETER SkipInstall
    Skip the dependency install step.

.PARAMETER Test
    Run the test suite instead of the server.

.EXAMPLE
    .\start.ps1

.EXAMPLE
    .\start.ps1 -Port 9000 -Test
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [string]$Database,
    [string]$BindAddress = '127.0.0.1',
    [string]$WorkspaceRoot,
    [switch]$SkipInstall,
    [switch]$Test
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'

function Write-Step($Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

# --- Virtual environment -----------------------------------------------------
if (-not (Test-Path $python)) {
    Write-Step 'Creating virtual environment'
    $bootstrap = (Get-Command py -ErrorAction SilentlyContinue)
    if ($bootstrap) { & py -3 -m venv $venv } else { & python -m venv $venv }
    if ($LASTEXITCODE -ne 0) { throw 'virtualenv_creation_failed: install Python 3.12+ and retry' }
}

if (-not $SkipInstall) {
    Write-Step 'Installing dependencies'
    & $python -m pip install --quiet --disable-pip-version-check -r (Join-Path $root 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'dependency_install_failed' }
}

# --- Tests -------------------------------------------------------------------
if ($Test) {
    Write-Step 'Running test suite'
    & $python -m pytest -q
    exit $LASTEXITCODE
}

# --- Configuration -----------------------------------------------------------
if (-not $Database) { $Database = Join-Path $root 'factory-v2.db' }
$env:FACTORY_DB = $Database
$env:PORT = "$Port"
$env:HOST = $BindAddress
if (-not $env:FACTORY_JWT_ISSUER) { $env:FACTORY_JWT_ISSUER = 'ace-factory-idp' }
if (-not $env:FACTORY_JWT_AUDIENCE) { $env:FACTORY_JWT_AUDIENCE = 'ace-factory' }
# Claude via the CVS cvscode gateway for all reasoning agents. AI_PROVIDER=none
# no longer "disables" anything usefully: there is no deterministic fallback, so
# every agent fails with ArtefactUnavailable and the factory produces nothing.
if (-not $env:AI_PROVIDER) { $env:AI_PROVIDER = 'gateway' }

# --- Launch ------------------------------------------------------------------
if ($WorkspaceRoot) { $env:FACTORY_WORKSPACE_ROOT = $WorkspaceRoot }
# If FACTORY_WORKSPACE_ROOT is not set here and not already in the environment,
# workspace.py will resolve an OS-appropriate default automatically.
Write-Step "Database $Database"
Write-Step "Open http://${BindAddress}:${Port} and sign in (or register the first account)"
Set-Location $root
& $python -m app.server
exit $LASTEXITCODE
