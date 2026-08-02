[CmdletBinding()]
param(
    [string]$RepositoryRoot,
    [string]$WebsiteRoot,
    [string]$InstalledRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Git {
    param(
        [Parameter(Mandatory)]
        [string]$Directory,
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $safeDirectory = [System.IO.Path]::GetFullPath($Directory).Replace("\", "/")
        $output = & git -c "safe.directory=$safeDirectory" -c core.excludesFile= -C $Directory @Arguments 2>$null
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        return $null
    }
    return @($output)
}

function Get-ValueOrUnknown {
    param([object]$Value)

    $values = @($Value)
    if ($null -eq $Value -or $values.Count -eq 0 -or [string]::IsNullOrWhiteSpace($values[0])) {
        return "unknown"
    }
    return [string]$values[0]
}

function Get-DirtyCounts {
    param([Parameter(Mandatory)][string]$Directory)

    $records = @(Invoke-Git -Directory $Directory -Arguments @("status", "--porcelain=v1", "--untracked-files=all"))
    $staged = 0
    $unstaged = 0
    $untracked = 0

    foreach ($record in $records) {
        if ($record.StartsWith("??")) {
            $untracked++
            continue
        }
        if ($record.Length -ge 2) {
            if ($record[0] -ne " ") { $staged++ }
            if ($record[1] -ne " ") { $unstaged++ }
        }
    }

    return [pscustomobject]@{
        Entries = $records.Count
        Staged = $staged
        Unstaged = $unstaged
        Untracked = $untracked
    }
}

function Get-UpstreamState {
    param([Parameter(Mandatory)][string]$Directory)

    $upstream = Get-ValueOrUnknown (Invoke-Git -Directory $Directory -Arguments @("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"))
    if ($upstream -eq "unknown") {
        return [pscustomobject]@{ Name = "none"; Ahead = "n/a"; Behind = "n/a" }
    }

    $counts = @(Invoke-Git -Directory $Directory -Arguments @("rev-list", "--left-right", "--count", "@{upstream}...HEAD"))
    $parts = if ($counts) { ([string]$counts[0]).Trim() -split "\s+" } else { @() }
    return [pscustomobject]@{
        Name = $upstream
        Behind = if ($parts.Count -eq 2) { $parts[0] } else { "unknown" }
        Ahead = if ($parts.Count -eq 2) { $parts[1] } else { "unknown" }
    }
}

function Write-RepositoryReport {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Directory
    )

    $gitRoot = Invoke-Git -Directory $Directory -Arguments @("rev-parse", "--show-toplevel")
    if (-not $gitRoot) {
        Write-Output "## $Label"
        Write-Output "- Status: not a Git repository"
        return
    }

    $branch = Get-ValueOrUnknown (Invoke-Git -Directory $Directory -Arguments @("symbolic-ref", "--quiet", "--short", "HEAD"))
    if ($branch -eq "unknown") { $branch = "detached" }
    $head = Get-ValueOrUnknown (Invoke-Git -Directory $Directory -Arguments @("rev-parse", "--short", "HEAD"))
    $version = Get-ValueOrUnknown (Invoke-Git -Directory $Directory -Arguments @("describe", "--tags", "--always", "--dirty"))
    $latestTag = Get-ValueOrUnknown (Invoke-Git -Directory $Directory -Arguments @("describe", "--tags", "--abbrev=0"))
    if ($latestTag -eq "unknown") { $latestTag = "none" }
    $dirty = Get-DirtyCounts -Directory $Directory
    $upstream = Get-UpstreamState -Directory $Directory

    Write-Output "## $Label"
    Write-Output "- Path: $(Get-ValueOrUnknown $gitRoot)"
    Write-Output "- Branch: $branch"
    Write-Output "- HEAD: $head"
    Write-Output "- Version: $version"
    Write-Output "- Latest tag: $latestTag"
    Write-Output "- Upstream: $($upstream.Name) (ahead $($upstream.Ahead), behind $($upstream.Behind))"
    Write-Output "- Dirty entries: $($dirty.Entries) (staged $($dirty.Staged), unstaged $($dirty.Unstaged), untracked $($dirty.Untracked))"
}

function Get-PackageVersion {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return "missing"
    }
    try {
        return [string]((Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json).version)
    } catch {
        return "unreadable"
    }
}

function Get-PyProjectVersion {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return "missing"
    }
    $match = Select-String -LiteralPath $Path -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($match -and $match.Matches[0].Groups.Count -gt 1) {
        return $match.Matches[0].Groups[1].Value
    }
    return "unreadable"
}

function Get-InstalledCoreVersion {
    param([Parameter(Mandatory)][string]$CoreDirectory)

    $python = Join-Path $CoreDirectory ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        return "not installed (project virtual environment is missing)"
    }
    $version = & $python -c "import importlib.metadata; print(importlib.metadata.version('collie-core'))" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) {
        return "not installed in project virtual environment"
    }
    return [string]$version
}

function Get-BuildState {
    param([Parameter(Mandatory)][string]$Path)

    if (Test-Path -LiteralPath $Path) {
        return "present"
    }
    return "missing"
}

function Get-WorktreeReport {
    param([Parameter(Mandatory)][string]$Directory)

    $worktreeLines = @(Invoke-Git -Directory $Directory -Arguments @("worktree", "list", "--porcelain"))
    $worktreePaths = @(
        foreach ($line in $worktreeLines) {
            if ($line.StartsWith("worktree ")) {
                $line.Substring(9)
            }
        }
    )

    Write-Output "## Worktrees"
    if ($worktreePaths.Count -eq 0) {
        Write-Output "- unavailable"
        return
    }

    foreach ($worktreePath in $worktreePaths) {
        $worktreeRoot = Invoke-Git -Directory $worktreePath -Arguments @("rev-parse", "--show-toplevel")
        if (-not $worktreeRoot) {
            Write-Output "- Path: $worktreePath; status unavailable"
            continue
        }
        $branch = Get-ValueOrUnknown (Invoke-Git -Directory $worktreePath -Arguments @("symbolic-ref", "--quiet", "--short", "HEAD"))
        if ($branch -eq "unknown") { $branch = "detached" }
        $head = Get-ValueOrUnknown (Invoke-Git -Directory $worktreePath -Arguments @("rev-parse", "--short", "HEAD"))
        $dirty = Get-DirtyCounts -Directory $worktreePath
        Write-Output "- Path: $(Get-ValueOrUnknown $worktreeRoot); branch $branch; HEAD $head; dirty entries $($dirty.Entries) (staged $($dirty.Staged), unstaged $($dirty.Unstaged), untracked $($dirty.Untracked))"
    }
}

function Get-ProvenanceState {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return "missing"
    }
    try {
        $provenance = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
        $valid =
            $provenance.schemaVersion -eq 1 -and
            $provenance.productVersion -is [string] -and
            $provenance.productVersion.Length -gt 0 -and
            $provenance.gitSha -is [string] -and
            $provenance.gitSha -match '^[0-9a-fA-F]{40,64}$' -and
            $provenance.dirty -is [bool] -and
            $provenance.builtAt -is [string] -and
            $provenance.builtAt.Length -gt 0
        if (-not $valid) {
            return "invalid"
        }
        return "valid (version $($provenance.productVersion), commit $($provenance.gitSha), dirty $($provenance.dirty), built $($provenance.builtAt))"
    } catch {
        return "unreadable"
    }
}

function Write-InstalledReport {
    param([Parameter(Mandatory)][string]$Directory)

    Write-Output "## Installed Collie"
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        Write-Output "- Status: supplied root not found"
        return
    }

    $installedRoot = (Resolve-Path -LiteralPath $Directory).Path
    $resources = Join-Path $installedRoot "resources"
    if (-not (Test-Path -LiteralPath $resources -PathType Container)) {
        $resources = $installedRoot
    }
    Write-Output "- Path: $installedRoot"
    Write-Output "- Electron executable (Collie.exe): $(Get-BuildState -Path (Join-Path $installedRoot "Collie.exe"))"
    Write-Output "- Electron resources: $(Get-BuildState -Path $resources)"
    Write-Output "- Electron app archive (app.asar): $(Get-BuildState -Path (Join-Path $resources "app.asar"))"
    Write-Output "- Packaged core runtime: $(Get-BuildState -Path (Join-Path $resources "collie-core"))"
    Write-Output "- Packaged build provenance: $(Get-ProvenanceState -Path (Join-Path $resources "collie-build-provenance.json"))"
    Write-Output "- Release artifact provenance: $(Get-ProvenanceState -Path (Join-Path $installedRoot "collie-artifact-provenance.json"))"
}

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Join-Path $PSScriptRoot ".."
}

$root = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$core = Join-Path $root "collie-core"
$desktop = Join-Path $root "collie-ui"

Write-RepositoryReport -Label "Main repository" -Directory $root

Write-Output ""
Get-WorktreeReport -Directory $root

Write-Output ""
if ([string]::IsNullOrWhiteSpace($WebsiteRoot)) {
    Write-Output "## Website repository (independent)"
    Write-Output "- Status: not requested; provide -WebsiteRoot to inspect an explicit checkout"
} elseif (Test-Path -LiteralPath $WebsiteRoot -PathType Container) {
    Write-RepositoryReport -Label "Website repository (independent)" -Directory $WebsiteRoot
} else {
    Write-Output "## Website repository (independent)"
    Write-Output "- Status: supplied root not found"
}

Write-Output ""
Write-Output "## Collie package and build"
Write-Output "- Core declared version: $(Get-PyProjectVersion -Path (Join-Path $core "pyproject.toml"))"
Write-Output "- Core installed version: $(Get-InstalledCoreVersion -CoreDirectory $core)"
Write-Output "- Desktop declared version: $(Get-PackageVersion -Path (Join-Path $desktop "package.json"))"
Write-Output "- Desktop build output (collie-ui/out): $(Get-BuildState -Path (Join-Path $desktop "out"))"
Write-Output "- Packaged-artifact directory (collie-ui/dist): $(Get-BuildState -Path (Join-Path $desktop "dist"))"

if (-not [string]::IsNullOrWhiteSpace($InstalledRoot)) {
    Write-Output ""
    Write-InstalledReport -Directory $InstalledRoot
}
