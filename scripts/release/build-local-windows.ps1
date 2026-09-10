# Diony AI - local Windows build. Double-click "Build Diony AI.bat" or run:
#   powershell -ExecutionPolicy Bypass -File scripts\release\build-local-windows.ps1
#
# The Windows twin of build-local-macos.sh: stages a secret-free payload, bundles a
# runtime with PyInstaller, lays the pinned ffmpeg + ffprobe beside the app, keeps the
# stable path dist\Diony AI\Diony AI.exe (a junction into dist\builds\<build-id>\), and opens it.
# Ad-hoc and local-only: unsigned, never publishable. Signed installers come from
# build-windows.ps1.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) { throw "$FilePath failed with exit code $LASTEXITCODE." }
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Version = (Get-Content (Join-Path $Root "packaging\desktop\version.json") -Raw | ConvertFrom-Json).version
$BuildRecipe = "windows-development-v1"
$Arch = if ([Environment]::Is64BitOperatingSystem) { "x86_64" } else { throw "Diony AI needs 64-bit Windows." }
$Output = Join-Path $Root "dist"
$Builds = Join-Path $Output "builds"
$StableApp = Join-Path $Output "Diony AI"
$StableReport = Join-Path $Output "build.json"

# Python 3.11+ : DIONY_LOCAL_PYTHON, the repo .venv, the py launcher, then python.
$VersionCheck = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
$LocalPython = $null
$LocalPrefix = @()
if ($env:DIONY_LOCAL_PYTHON) { $LocalPython = $env:DIONY_LOCAL_PYTHON }
elseif (Test-Path (Join-Path $Root ".venv\Scripts\python.exe")) { $LocalPython = Join-Path $Root ".venv\Scripts\python.exe" }
elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    foreach ($Candidate in @("-3.14", "-3.13", "-3.12", "-3.11")) {
        & py.exe $Candidate -c $VersionCheck *> $null
        if ($LASTEXITCODE -eq 0) { $LocalPython = "py.exe"; $LocalPrefix = @($Candidate); break }
    }
}
elseif (Get-Command python.exe -ErrorAction SilentlyContinue) { $LocalPython = "python.exe" }
if (-not $LocalPython) { throw "Diony AI local builds need Python 3.11 or newer: https://www.python.org/downloads/windows/" }
& $LocalPython @($LocalPrefix + @("-c", $VersionCheck)) *> $null
if ($LASTEXITCODE -ne 0) { throw "Diony AI local builds need Python 3.11 or newer (found $LocalPython)." }

$BuildRoot = Join-Path ([IO.Path]::GetTempPath()) ("diony-local-win-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $BuildRoot | Out-Null
try {
    # Stage the payload and hash it; an identical payload means the current build is current.
    $Payload = Join-Path $BuildRoot "payload"
    $PreviousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = Join-Path $Root "apps\desk\src"
        Invoke-Checked $LocalPython ($LocalPrefix + @("-m", "diony_desk.release", "stage", $Root, $Payload, "--version", $Version))
    }
    finally { $env:PYTHONPATH = $PreviousPythonPath }
    $PayloadSha = (Get-Content (Join-Path $Payload "desktop-release.json") -Raw | ConvertFrom-Json).payload_sha256
    if ((Test-Path $StableReport) -and (Test-Path (Join-Path $StableApp "Diony AI.exe"))) {
        $Report = Get-Content $StableReport -Raw | ConvertFrom-Json
        if ($Report.version -eq $Version -and $Report.architecture -eq $Arch -and $Report.payload_sha256 -eq $PayloadSha -and $Report.build_recipe -eq $BuildRecipe) {
            Write-Output "Local Diony AI $Version is already current."
            Start-Process -FilePath (Join-Path $StableApp "Diony AI.exe") -WorkingDirectory $StableApp
            exit 0
        }
    }

    # A clean venv with the release requirements and the staged packages.
    $Venv = Join-Path $BuildRoot "venv"
    Invoke-Checked $LocalPython ($LocalPrefix + @("-m", "venv", $Venv))
    $Py = Join-Path $Venv "Scripts\python.exe"
    $InstallPayload = Join-Path $BuildRoot "install-payload"
    Copy-Item -LiteralPath $Payload -Destination $InstallPayload -Recurse
    Invoke-Checked $Py @("-m", "pip", "install", "--disable-pip-version-check", "-r", (Join-Path $Root "packaging\desktop\requirements-release.txt"))
    Invoke-Checked $Py @(
        "-m", "pip", "install", "--disable-pip-version-check",
        (Join-Path $InstallPayload "packages\contracts"), (Join-Path $InstallPayload "apps\wizard"),
        (Join-Path $InstallPayload "apps\producer"), (Join-Path $InstallPayload "apps\director"),
        (Join-Path $InstallPayload "apps\desk")
    )

    # ffmpeg comes with Diony: the pinned Windows release, cached and SHA-256 verified.
    $FfmpegStage = Join-Path $BuildRoot "ffmpeg"
    try {
        $env:PYTHONPATH = Join-Path $Root "apps\desk\src"
        Invoke-Checked $Py @("-m", "diony_desk.media", "fetch", "windows-amd64", $FfmpegStage)
    }
    finally { $env:PYTHONPATH = $PreviousPythonPath }
    if (-not (Test-Path (Join-Path $FfmpegStage "ffmpeg.exe")) -or -not (Test-Path (Join-Path $FfmpegStage "ffprobe.exe"))) {
        throw "The pinned ffmpeg release did not stage for windows-amd64."
    }

    $env:DIONY_FFMPEG_BUNDLE_DIR = $FfmpegStage
    $env:DIONY_DESKTOP_PAYLOAD = $Payload
    $env:DIONY_DESKTOP_VERSION = $Version
    Invoke-Checked $Py @(
        "-m", "PyInstaller", "--clean", "--noconfirm",
        "--distpath", (Join-Path $BuildRoot "dist"),
        "--workpath", (Join-Path $BuildRoot "work"),
        (Join-Path $Root "packaging\desktop\diony.spec")
    )
    $BuiltApp = Join-Path $BuildRoot "dist\Diony AI"
    if (-not (Test-Path (Join-Path $BuiltApp "Diony AI.exe"))) { throw "PyInstaller did not produce Diony AI.exe." }
    $FfmpegHome = $null
    foreach ($Candidate in @((Join-Path $BuiltApp "_internal"), $BuiltApp)) {
        if ((Test-Path (Join-Path $Candidate "ffmpeg.exe")) -and (Test-Path (Join-Path $Candidate "ffprobe.exe"))) { $FfmpegHome = $Candidate; break }
    }
    if (-not $FfmpegHome) { throw "Diony AI was built without ffmpeg in its runtime folder." }
    & (Join-Path $FfmpegHome "ffmpeg.exe") -version | Select-Object -First 1

    $BuiltAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $BuildId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" + $PayloadSha.Substring(0, 12) + "-" + $Arch
    $FinalDir = Join-Path $Builds $BuildId
    if (Test-Path $FinalDir) { throw "Local build already exists: $FinalDir" }
    New-Item -ItemType Directory -Path $FinalDir | Out-Null
    Move-Item -LiteralPath $BuiltApp -Destination (Join-Path $FinalDir "Diony AI")
    try {
        $env:PYTHONPATH = Join-Path $Root "apps\desk\src"
        Invoke-Checked $Py @(
            "-m", "diony_desk.release", "development-report",
            "--app", (Join-Path $FinalDir "Diony AI"),
            "--payload-metadata", (Join-Path $Payload "desktop-release.json"),
            "--version", $Version, "--architecture", $Arch, "--built-at", $BuiltAt,
            "--output", (Join-Path $FinalDir "build.json")
        )
    }
    finally { $env:PYTHONPATH = $PreviousPythonPath }

    # The stable path is a directory junction (no admin needed) plus a copied report.
    New-Item -ItemType Directory -Path $Output -Force | Out-Null
    # delete only the junction itself, never the build it points at
    if (Test-Path $StableApp) { [IO.Directory]::Delete($StableApp) }
    New-Item -ItemType Junction -Path $StableApp -Target (Join-Path $FinalDir "Diony AI") | Out-Null
    Copy-Item -LiteralPath (Join-Path $FinalDir "build.json") -Destination $StableReport -Force
    # Git keeps only the current build under dist\builds\ (the root .gitignore opens
    # dist\Diony.app, dist\build.json, dist\current-build.txt and dist\builds\);
    # builds\.gitignore names the one build to keep, current-build.txt names it
    # for the launchers (the junction itself is not committed).
    @("# Written by the local build script: only the current build is committed.", "*", "!.gitignore", "!$BuildId/", "!$BuildId/**") |
        Set-Content (Join-Path $Builds ".gitignore") -Encoding ascii
    Set-Content (Join-Path $Output "current-build.txt") -Value $BuildId -Encoding ascii

    # Prune old local builds, keeping the newest DIONY_KEEP_LOCAL_BUILDS (default 5).
    $Keep = if ($env:DIONY_KEEP_LOCAL_BUILDS) { [int]$env:DIONY_KEEP_LOCAL_BUILDS } else { 5 }
    Get-ChildItem $Builds -Directory | Sort-Object Name -Descending | Select-Object -Skip $Keep | ForEach-Object {
        if ($_.Name -ne $BuildId) { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    }

    Write-Output "Built local-only Diony AI $Version for $Arch."
    Write-Output "This app is not code-signed or publishable."
    Write-Output (Join-Path $StableApp "Diony AI.exe")
    Start-Process -FilePath (Join-Path $StableApp "Diony AI.exe") -WorkingDirectory $StableApp
}
finally {
    if (Test-Path $BuildRoot) { Remove-Item -LiteralPath $BuildRoot -Recurse -Force }
}
