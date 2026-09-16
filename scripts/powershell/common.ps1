Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-RelicAgentWsl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptName,

        [string]$Distro = "",

        [string]$RepoPath = "",

        [string[]]$ForwardedArguments = @()
    )

    $wsl = Get-Command "wsl.exe" -ErrorAction SilentlyContinue
    if ($null -eq $wsl) {
        throw "wsl.exe was not found. Install WSL2 and run Relic Agent through a Linux distribution."
    }

    if ([string]::IsNullOrWhiteSpace($RepoPath)) {
        $RepoPath = $env:RELIC_AGENT_WSL_REPO
    }
    if ([string]::IsNullOrWhiteSpace($RepoPath)) {
        $RepoPath = "~/relic-agent"
    }

    $wslArguments = @()
    if (-not [string]::IsNullOrWhiteSpace($Distro)) {
        $wslArguments += @("--distribution", $Distro)
    }
    $wslArguments += @(
        "--cd", $RepoPath,
        "--", "bash", "scripts/bash/$ScriptName"
    )
    $wslArguments += $ForwardedArguments

    & $wsl.Source @wslArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Relic Agent WSL command failed with exit code $LASTEXITCODE."
    }
}
