$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Release = Get-Content (Join-Path $Root "packaging\desktop\version.json") -Raw | ConvertFrom-Json
$Version = $Release.version
$Output = Join-Path $Root "dist\desktop\$Version"
$Artifact = Join-Path $Output "DionyAI-Setup-$Version-windows-x64.exe"
$Certificate = $env:DIONY_WINDOWS_CERT_SHA1
$TimestampUrl = $env:DIONY_WINDOWS_TIMESTAMP_URL
$ReleasePython = $env:DIONY_RELEASE_PYTHON
$SignTool = if ($env:DIONY_SIGNTOOL) { $env:DIONY_SIGNTOOL } else { "signtool.exe" }
$Iscc = if ($env:DIONY_ISCC) { $env:DIONY_ISCC } else { "ISCC.exe" }

if (-not $Certificate -or -not $TimestampUrl) {
    throw "Set DIONY_WINDOWS_CERT_SHA1 and DIONY_WINDOWS_TIMESTAMP_URL."
}
if (Test-Path $Artifact) { throw "Refusing to overwrite existing release: $Artifact" }
Get-Command $SignTool -ErrorAction Stop | Out-Null
Get-Command $Iscc -ErrorAction Stop | Out-Null

$BootstrapFile = $null
$BootstrapPrefix = @()
$VersionCheck = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($ReleasePython) {
    Get-Command $ReleasePython -ErrorAction Stop | Out-Null
    Invoke-Checked $ReleasePython @("-c", $VersionCheck)
    $BootstrapFile = $ReleasePython
}
else {
    Get-Command py.exe -ErrorAction Stop | Out-Null
    foreach ($Candidate in @("-3.14", "-3.13", "-3.12", "-3.11")) {
        & py.exe $Candidate -c $VersionCheck *> $null
        if ($LASTEXITCODE -eq 0) {
            $BootstrapFile = "py.exe"
            $BootstrapPrefix = @($Candidate)
            break
        }
    }
}
if (-not $BootstrapFile) {
    throw "Set DIONY_RELEASE_PYTHON to Python 3.11 or newer."
}

$BuildRoot = Join-Path ([IO.Path]::GetTempPath()) ("diony-windows-release-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $BuildRoot | Out-Null
try {
    $Python = Join-Path $BuildRoot "venv\Scripts\python.exe"
    Invoke-Checked $BootstrapFile ($BootstrapPrefix + @("-m", "venv", (Join-Path $BuildRoot "venv")))
    Invoke-Checked $Python @("-m", "pip", "install", "--disable-pip-version-check", "-r", (Join-Path $Root "packaging\desktop\requirements-release.txt"))
    $Payload = Join-Path $BuildRoot "payload"
    $PreviousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = Join-Path $Root "apps\desk\src"
        Invoke-Checked $Python @("-m", "diony_desk.release", "stage", $Root, $Payload, "--version", $Version)
    }
    finally {
        $env:PYTHONPATH = $PreviousPythonPath
    }
    $InstallPayload = Join-Path $BuildRoot "install-payload"
    Copy-Item -LiteralPath $Payload -Destination $InstallPayload -Recurse
    Invoke-Checked $Python @(
        "-m", "pip", "install", "--disable-pip-version-check",
        (Join-Path $InstallPayload "packages\contracts"), (Join-Path $InstallPayload "apps\wizard"),
        (Join-Path $InstallPayload "apps\producer"), (Join-Path $InstallPayload "apps\director"),
        (Join-Path $InstallPayload "apps\desk")
    )
    # ffmpeg comes with Diony: the pinned Windows release, SHA-256 verified; the
    # signing pass below covers ffmpeg.exe and ffprobe.exe like every other .exe.
    $FfmpegStage = Join-Path $BuildRoot "ffmpeg"
    try {
        $env:PYTHONPATH = Join-Path $Root "apps\desk\src"
        Invoke-Checked $Python @("-m", "diony_desk.media", "fetch", "windows-amd64", $FfmpegStage)
    }
    finally {
        $env:PYTHONPATH = $PreviousPythonPath
    }
    if (-not (Test-Path (Join-Path $FfmpegStage "ffmpeg.exe")) -or -not (Test-Path (Join-Path $FfmpegStage "ffprobe.exe"))) {
        throw "The pinned ffmpeg release did not stage for windows-amd64."
    }
    $env:DIONY_FFMPEG_BUNDLE_DIR = $FfmpegStage
    $env:DIONY_DESKTOP_PAYLOAD = $Payload
    $env:DIONY_DESKTOP_VERSION = $Version
    Invoke-Checked $Python @(
        "-m", "PyInstaller", "--clean", "--noconfirm",
        "--distpath", (Join-Path $BuildRoot "dist"),
        "--workpath", (Join-Path $BuildRoot "work"),
        (Join-Path $Root "packaging\desktop\diony.spec")
    )
    $App = Join-Path $BuildRoot "dist\Diony AI"
    if (-not (Test-Path (Join-Path $App "Diony AI.exe"))) { throw "PyInstaller did not produce Diony AI.exe" }
    if (-not (Test-Path (Join-Path $App "_internal\ffmpeg.exe")) -or -not (Test-Path (Join-Path $App "_internal\ffprobe.exe"))) {
        throw "Diony AI was built without ffmpeg in its runtime folder."
    }

    Get-ChildItem $App -Recurse -File | Where-Object { $_.Extension -in ".exe", ".dll", ".pyd" } | ForEach-Object {
        Invoke-Checked $SignTool @("sign", "/sha1", $Certificate, "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", $_.FullName)
        Invoke-Checked $SignTool @("verify", "/pa", "/all", $_.FullName)
    }
    New-Item -ItemType Directory -Path $Output -Force | Out-Null
    Invoke-Checked $Iscc @("/DSourceDir=$App", "/DOutputDir=$Output", "/DAppVersion=$Version", (Join-Path $Root "packaging\windows\DionyAI.iss"))
    Invoke-Checked $SignTool @("sign", "/sha1", $Certificate, "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", $Artifact)
    Invoke-Checked $SignTool @("verify", "/pa", "/all", $Artifact)
    Invoke-Checked $Python @("-m", "pip", "freeze", "--all") | Set-Content (Join-Path $Output "windows-python-lock.txt") -Encoding ascii
    (Get-FileHash -Algorithm SHA256 $Artifact).Hash.ToLowerInvariant() + "  " + (Split-Path $Artifact -Leaf) |
        Set-Content "$Artifact.sha256" -Encoding ascii
    '{"signed":true,"timestamped":true,"signature_verified":true}' |
        Set-Content (Join-Path $Output "windows-proof.json") -Encoding ascii
    Write-Output $Artifact
}
finally {
    if (Test-Path $BuildRoot) { Remove-Item -LiteralPath $BuildRoot -Recurse -Force }
}
