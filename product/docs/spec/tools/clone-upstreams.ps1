param(
    [string]$Destination = (Join-Path (Get-Location) 'agent-platform-upstream'),
    [ValidateSet('core', 'research', 'evaluation', 'training')]
    [string[]]$Groups = @('core')
)
$ErrorActionPreference = 'Stop'
$catalogPath = Join-Path $PSScriptRoot '../contracts/components.json'
$catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git is required.' }
$resolvedDestination = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Path $resolvedDestination -Force | Out-Null
foreach ($component in $catalog.components) {
    if (-not $component.clone_group -or $component.clone_group -notin $Groups) { continue }
    $target = Join-Path $resolvedDestination $component.id
    if (Test-Path -LiteralPath $target) {
        Write-Output "SKIP existing directory: $target"
        continue
    }
    Write-Output "CLONE $($component.repository) -> $target"
    & git clone --filter=blob:none "$($component.repository).git" $target
    if ($LASTEXITCODE -ne 0) { throw "Clone failed for $($component.id); existing partial files are preserved." }
}
Write-Output 'Done. No existing working tree was changed; reading commits were not automatically checked out.'
