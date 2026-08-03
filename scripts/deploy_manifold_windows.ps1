[CmdletBinding()]
param(
    [string]$Repository = "",
    [string]$PsdkRoot = "",
    [string]$OutputDirectory = "",
    [string]$OfflineWheels = "",
    [string]$CredentialHeader = "",
    [string]$PackagePath = "",
    [string]$SshHost = "gap-plot-manifold",
    [string]$RemoteDirectory = "/home/dji/gap_plot_ai_transfer"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "This deployment entry point must run on Windows."
}

$ScriptRepository = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $Repository) {
    $Repository = $ScriptRepository
}
$Repository = (Resolve-Path -LiteralPath $Repository).Path
if (-not $CredentialHeader) {
    $CredentialHeader = Join-Path $Repository "config\dji_sdk_app_info.local.h"
}
if (-not $PsdkRoot) {
    $PsdkRoot = Join-Path (Split-Path -Parent $Repository) "Payload-SDK-3.16.0"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path (Split-Path -Parent $Repository) "Gap-Plot-Deploy-Packages-LF"
}
if (-not $OfflineWheels) {
    $OfflineWheels = Join-Path (Split-Path -Parent $Repository) "Gap-Plot-Offline-Assets\offline_wheels"
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path

Push-Location $Repository
try {
    $branch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "feature/pilot-liveview-inference") {
        throw "Checkout feature/pilot-liveview-inference before packaging."
    }
    $status = & git status --porcelain --untracked-files=all
    if ($LASTEXITCODE -ne 0 -or $status) {
        throw "Repository must be clean. The ignored local credential file is allowed."
    }
    $python = (Get-Command python -ErrorAction Stop).Source
    if ($PackagePath) {
        $package = (Resolve-Path -LiteralPath $PackagePath).Path
    }
    else {
        $PsdkRoot = (Resolve-Path -LiteralPath $PsdkRoot).Path
        $CredentialHeader = (Resolve-Path -LiteralPath $CredentialHeader).Path
        $OfflineWheels = (Resolve-Path -LiteralPath $OfflineWheels).Path
        & $python scripts\psdk_app_info.py $CredentialHeader
        if ($LASTEXITCODE -ne 0) { throw "PSDK credential validation failed." }

        & $python -m pytest tests\test_offline_deployment.py tests\test_archive_verifier.py -q
        if ($LASTEXITCODE -ne 0) { throw "Offline packaging tests failed." }

        $arguments = @(
            "scripts\create_offline_deployment.py",
            "--psdk-root", $PsdkRoot,
            "--credentials", $CredentialHeader,
            "--wheels", $OfflineWheels,
            "--output", $OutputDirectory
        )
        $builderOutput = & $python @arguments
        if ($LASTEXITCODE -ne 0) { throw "Offline package generation failed." }
        $packageLine = $builderOutput | Where-Object { $_ -like "package=*" }
        if (@($packageLine).Count -ne 1) { throw "Builder did not return one package path." }
        $package = $packageLine.Substring("package=".Length)
    }
    $sidecar = "$package.sha256"
    if (-not (Test-Path -LiteralPath $package) -or -not (Test-Path -LiteralPath $sidecar)) {
        throw "New package or checksum sidecar is missing."
    }
    if ((Split-Path -Leaf $package) -like "gap_plot_ai_offline_21f56625f908_20260803T090552Z*") {
        throw "The invalid legacy package was selected."
    }

    & $python scripts\verify_offline_archive.py $package --sidecar $sidecar
    if ($LASTEXITCODE -ne 0) { throw "Archive verification failed." }
    $outerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $package).Hash.ToLowerInvariant()
    $sidecarHash = ((Get-Content -LiteralPath $sidecar -Raw).Trim() -split "\s+")[0].ToLowerInvariant()
    if ($outerHash -ne $sidecarHash) { throw "Outer SHA-256 mismatch." }

    & ssh -o BatchMode=yes -o ConnectTimeout=10 $SshHost "uname -n && uname -m"
    if ($LASTEXITCODE -ne 0) { throw "Read-only SSH connection check failed." }
    & ssh -o BatchMode=yes $SshHost "mkdir -p '$RemoteDirectory'"
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the transfer directory." }
    & scp -- $package $sidecar "${SshHost}:$RemoteDirectory/"
    if ($LASTEXITCODE -ne 0) { throw "SCP transfer failed." }

    $archiveName = Split-Path -Leaf $package
    Write-Host "Transferred verified package: $archiveName"
    Write-Host "Next, connect with: ssh $SshHost"
    Write-Host "Then follow docs/MANIFOLD_FIRST_RUN.md using: $RemoteDirectory/$archiveName"
}
finally {
    Pop-Location
}
