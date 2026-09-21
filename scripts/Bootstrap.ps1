param([ValidateSet('run','repair','diagnose','verify')][string]$Mode = 'run')
# Prefer this PowerShell's standard modules when launched through another shell.
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$Runtime = Join-Path $Root '.runtime'
$Logs = Join-Path $Root 'logs'
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$Lock = $null
$Transcript = $false
$ExitCode = 0

function Write-Utf8([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, $script:Utf8)
}
function Run-Command([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Program $($Arguments -join ' ')" }
}
function Get-VerifiedDownload([string]$Url, [string]$Destination, [string]$Sha256 = '') {
    if ((Test-Path -LiteralPath $Destination) -and $Sha256) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Sha256) { return }
    }
    $Part = "$Destination.partial"
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        try {
            Write-Host "Downloading: $Url"
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Part -TimeoutSec 1200
            if ($Sha256) {
                $Actual = (Get-FileHash -LiteralPath $Part -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($Actual -ne $Sha256) { throw 'SHA-256 mismatch. The downloaded file will NOT be executed.' }
            }
            Move-Item -LiteralPath $Part -Destination $Destination -Force
            return
        } catch {
            if ($Attempt -eq 3) { throw }
            Write-Host "Download failed; retry $Attempt/3 in a few seconds."
            Start-Sleep -Seconds (2 * $Attempt)
        }
    }
}

function Test-RunningStudio {
    try {
        $RecordPath = Join-Path $Runtime 'server.json'
        if (-not (Test-Path -LiteralPath $RecordPath)) { return $false }
        $Record = Get-Content -LiteralPath $RecordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([IO.Path]::GetFullPath([string]$Record.root) -ne [IO.Path]::GetFullPath($Root)) { return $false }
        $Address = [Uri]([string]$Record.url)
        if ($Address.Scheme -ne 'http' -or $Address.Host -ne '127.0.0.1' -or $Address.AbsolutePath -ne '/' -or $Address.Query -or $Address.Fragment -or $Address.UserInfo) { return $false }
        $Health = Invoke-RestMethod -Uri ($Record.url + '/api/health') -TimeoutSec 2
        if ($Health.app -ne 'cvELD Image Studio' -or -not $Record.instance_id -or $Health.instance_id -ne $Record.instance_id) { return $false }
        return $true
    } catch { return $false }
}

try {
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        throw 'This package requires Windows x64 (Intel/AMD CPU), not Windows ARM.'
    }
    if ($Root.StartsWith('\\')) { throw 'Extract to a local NTFS SSD, not a network share.' }
    New-Item -ItemType Directory -Force -Path $Runtime,$Logs | Out-Null
    try { $Lock = [IO.File]::Open((Join-Path $Runtime 'bootstrap.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch {
        $LockError = $_
        $NativeCode = $_.Exception.InnerException.HResult -band 0xffff
        if ($NativeCode -notin @(32,33)) { throw $LockError }
        throw 'The app/setup is running. Close its console before Repair/Diagnose/Verify. / アプリ終了後に実行してください。'
    }
    if ($Mode -eq 'run' -and (Test-RunningStudio)) { throw 'Already running. Close the original console first. / 起動済みです。先に元のDOS窓を閉じてください。' }
    Start-Transcript -Path (Join-Path $Logs ('setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')) | Out-Null
    $Transcript = $true
    Write-Host "`n=== cvELD Image Studio | Qwen-Image-2.1 | $Mode ===`n"
    Write-Host 'NVIDIA drivers are checked, never automatically replaced. Internet is needed for first setup.'
    Write-Host 'Model download: approximately 33 GB. Recommend 64 GB RAM and 80-100 GB free SSD space.'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONNOUSERSITE = '1'
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $env:UV_CACHE_DIR = Join-Path $Runtime 'uv-cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtime 'python'
    $env:UV_PYTHON_BIN_DIR = Join-Path $Runtime 'bin'
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $env:UV_LINK_MODE = 'copy'
    $env:UV_HTTP_TIMEOUT = '1200'
    $env:HF_HOME = Join-Path $Runtime 'hf'
    $env:HF_HUB_DISABLE_TELEMETRY = '1'
    $env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
    $env:DO_NOT_TRACK = '1'
    $DownloadDir = Join-Path $Runtime 'downloads'
    $LicenseDir = Join-Path $Runtime 'licenses'
    New-Item -ItemType Directory -Force -Path $DownloadDir,$LicenseDir | Out-Null

    # Download the actual license, but do not silently accept it on the user's behalf.
    $ModelRevision = '790c92633540aa0cb11d9abf19eb46d861714758'
    $License = Join-Path $LicenseDir 'Qwen-Image-2.1-LICENSE.txt'
    $LicenseUrl = "https://huggingface.co/Qwen/Qwen-Image-2.1/resolve/$ModelRevision/LICENSE"
    if (-not (Test-Path -LiteralPath $License)) { Get-VerifiedDownload $LicenseUrl $License }
    $LicenseHash = (Get-FileHash -LiteralPath $License -Algorithm SHA256).Hash.ToLowerInvariant()
    $ConsentFile = Join-Path $Runtime 'license-consent.json'
    $ConsentOK = $false
    if (Test-Path -LiteralPath $ConsentFile) {
        try {
            $Consent = Get-Content -LiteralPath $ConsentFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $ConsentOK = ($Consent.license_sha256 -eq $LicenseHash -and $Consent.model_revision -eq $ModelRevision)
        } catch { $ConsentOK = $false }
    }
    if (-not $ConsentOK) {
        Write-Host "`nIMPORTANT: Qwen Research License; research/evaluation only."
        Write-Host 'Commercial use requires a separate license from Qwen. Free download does NOT mean unrestricted use.'
        Write-Host "License: $License"
        Write-Host "Japanese explanation: $Root\docs\Manual_ja.html"
        Start-Process -FilePath 'notepad.exe' -ArgumentList ('"' + $License + '"')
        $Answer = (Read-Host 'Accept the license and continue? [y/n]').Trim()
        if ($Answer -ine 'y') { throw 'Setup cancelled. No model weights were downloaded.' }
        Write-Utf8 $ConsentFile ((@{ model_revision=$ModelRevision; license_sha256=$LicenseHash;
            purpose=$Answer; accepted_at=(Get-Date).ToUniversalTime().ToString('o'); source=$LicenseUrl } | ConvertTo-Json))
    }

    # uv standalone is pinned AND hash-verified before it is executed.
    $UvVersion = '0.12.17'
    $UvSha = 'a252121d5b59398fcb137c6ea448176459a44010f33f67e0072305a637119ca7'
    $UvDir = Join-Path $Runtime "uv-$UvVersion"
    $Uv = Join-Path $UvDir 'uv.exe'
    if (-not (Test-Path -LiteralPath $Uv)) {
        $Zip = Join-Path $DownloadDir "uv-$UvVersion.zip"
        Get-VerifiedDownload "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" $Zip $UvSha
        New-Item -ItemType Directory -Force -Path $UvDir | Out-Null
        Expand-Archive -LiteralPath $Zip -DestinationPath $UvDir -Force
        if (-not (Test-Path -LiteralPath $Uv)) {
            $Found = Get-ChildItem -LiteralPath $UvDir -Filter 'uv.exe' -Recurse | Select-Object -First 1
            if (-not $Found) { throw 'uv.exe was not present in the verified archive.' }
            Copy-Item -LiteralPath $Found.FullName -Destination $Uv
        }
    }
    Run-Command $Uv @('--version')

    # Microsoft VC++ runtime, only if missing. Authenticode is checked before execution.
    $VcPresent = $false
    try { $VcPresent = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64').Installed -eq 1 } catch {}
    if (-not $VcPresent) {
        Write-Host 'Microsoft Visual C++ x64 runtime is missing. Windows may show an elevation prompt.'
        $VcExe = Join-Path $DownloadDir 'vc_redist.x64.exe'
        Get-VerifiedDownload 'https://aka.ms/vc14/vc_redist.x64.exe' $VcExe
        $Signature = Get-AuthenticodeSignature -LiteralPath $VcExe
        if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
            throw 'Microsoft runtime signature verification failed; installer was not executed.'
        }
        $VcProcess = Start-Process -FilePath $VcExe -ArgumentList @('/install','/passive','/norestart') -Wait -PassThru
        if ($VcProcess.ExitCode -notin @(0,1638,3010)) { throw "VC++ runtime install failed: $($VcProcess.ExitCode)" }
        if ($VcProcess.ExitCode -eq 3010) { Write-Host 'Windows requested a restart. If DLL loading fails, restart and run Run.bat again.' }
    }
    $Venv = Join-Path $Root '.venv'
    $Python = Join-Path $Venv 'Scripts\python.exe'
    $Ready = Join-Path $Runtime 'environment.json'
    $Freeze = Join-Path $Runtime 'requirements.resolved.txt'
    if ($Mode -eq 'repair') {
        Write-Host 'Repair: rebuilding ONLY this app virtual environment; models and generated images are preserved.'
        if (Test-Path -LiteralPath $Venv) { Remove-Item -LiteralPath $Venv -Recurse -Force }
        if (Test-Path -LiteralPath $Ready) { Remove-Item -LiteralPath $Ready -Force }
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        Run-Command $Uv @('python','install','3.12')
        Run-Command $Uv @('venv','--python','3.12',$Venv)
    }
    $DiffusersSha = '6256aa7666cedd47443adc8f82da9a10e110b09c'
    $InputHash = (Get-FileHash (Join-Path $Root 'requirements.in') -Algorithm SHA256).Hash +
                 (Get-FileHash (Join-Path $Root 'constraints.txt') -Algorithm SHA256).Hash + $DiffusersSha
    $NeedInstall = $true
    if (Test-Path -LiteralPath $Ready) {
        try {
            $Old = Get-Content -LiteralPath $Ready -Raw -Encoding UTF8 | ConvertFrom-Json
            $NeedInstall = ($Old.input_hash -ne $InputHash -or $Old.root -ne $Root)
        } catch {}
    }
    if ($NeedInstall) {
        Write-Host "`n[1/4] Installing PyTorch CUDA 12.8 for Blackwell (no separate CUDA Toolkit needed)."
        Run-Command $Uv @('pip','install','--python',$Python,'--index-url','https://download.pytorch.org/whl/cu128',
            'torch==2.11.0','torchvision==0.26.0')
        Write-Host "`n[2/4] Installing pinned Diffusers / Transformers and WebUI dependencies."
        $Requirement = Join-Path $Root 'requirements.in'
        $Extra = @("diffusers @ https://github.com/huggingface/diffusers/archive/$DiffusersSha.zip")
        if ($Mode -eq 'repair' -and (Test-Path -LiteralPath $Freeze)) {
            # Reuse the resolved helper versions, while torch/torchvision came from their official index above.
            $Locked = (Get-Content -LiteralPath $Freeze -Encoding UTF8 | Where-Object { $_ -notmatch '^torch(vision)?==' }) -join "`n"
            $Requirement = Join-Path $Runtime 'repair-requirements.txt'
            Write-Utf8 $Requirement $Locked
            $Extra = @()
        }
        $InstallArgs = @('pip','install','--python',$Python,'--index-url','https://pypi.org/simple',
            '--constraint',(Join-Path $Root 'constraints.txt'),'--requirements',$Requirement) + $Extra
        Run-Command $Uv $InstallArgs
        Run-Command $Uv @('pip','check','--python',$Python)
        $Resolved = (& $Uv pip freeze --python $Python) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'Failed to record resolved package versions.' }
        Write-Utf8 $Freeze $Resolved
        Write-Utf8 $Ready ((@{input_hash=$InputHash;root=$Root;uv=$UvVersion;diffusers_revision=$DiffusersSha;
            installed_at=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json))
    }
    Write-Host "`n[3/4] Checking imports and running a real CUDA BF16 / attention kernel test."
    Run-Command $Python @('-m','studio.doctor')
    if ($Mode -ne 'diagnose') {
        Write-Host "`n[4/4] Downloading / checking the pinned official model (resume supported)."
        $ArgsForModel = @('-m','studio.model_download')
        if ($Mode -eq 'verify') { $ArgsForModel += '--verify' }
        Run-Command $Python $ArgsForModel
    }
    if ($Mode -in @('run','repair')) {
        Write-Host "`nSetup complete. Starting the local browser UI. Close with Ctrl+C."
        Write-Host 'Keep this console open. Closing it stops Image Studio. / このDOS窓を閉じるとツールも終了します。'
        $env:CVELD_STUDIO_OWNER_PID = [string]$PID
        Run-Command $Python @('-m','studio.launch')
    } else { Write-Host "`nCompleted. Reports are in .runtime and logs." }
} catch {
    $ExitCode = 1
    Write-Host "`nERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'See logs/setup-*.log and docs/Manual_ja.html. Re-running Run.bat reuses completed downloads.'
    Write-Host 'NVIDIA driver updates, access permissions, proxy authentication, and Windows restart prompts need your action.'
} finally {
    if ($Transcript) { Stop-Transcript | Out-Null }
    if ($Lock) { $Lock.Dispose() }
}
exit $ExitCode
