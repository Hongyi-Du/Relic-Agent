param(
    [string]$Distro = "",
    [string]$RepoPath = "",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ForwardedArguments = @()
)

. "$PSScriptRoot/common.ps1"
Invoke-RelicAgentWsl -ScriptName "smoke.sh" -Distro $Distro -RepoPath $RepoPath -ForwardedArguments $ForwardedArguments
