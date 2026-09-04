<#
.SYNOPSIS
    Universal NemoHeadUnit Distribution & Deployment Script for PowerShell.

.DESCRIPTION
    Intelligently inspects the target system (Local Windows, Local Linux via pwsh, or Remote SSH target),
    detects operating system, CPU architecture, GPU hardware, and environment runtimes,
    and deploys NemoHeadUnit with optimal dependencies and configurations.
    Supports bidirectional cross-platform deployment (Windows -> Windows, Windows -> Linux).

.PARAMETER Local
    Deploy and configure directly on the local machine.

.PARAMETER Target
    Remote target in format '[user@]hostname' or IP address.

.PARAMETER Method
    Deployment engine: 'auto' (default), 'micromamba', or 'venv'.

.PARAMETER Dest
    Target installation directory. Default: 'C:\NemoHeadUnit-Wireless' on Windows, '/opt/nemo-headunit' on Linux.

.PARAMETER DryRun
    Inspect and probe target environment without modifying files or packages.

.PARAMETER SkipDeps
    Skip package and Python dependency installations (fast code sync).

.PARAMETER SkipHardwareFixes
    Skip hardware adaptation and quirks scripts.

.PARAMETER SkipService
    Skip installation of systemd unit or desktop shortcuts.

.PARAMETER Restart
    Automatically restart/launch NemoHeadUnit application after deployment.

.PARAMETER Port
    Custom SSH port for remote connections.

.PARAMETER IdentityFile
    Custom SSH private key file.

.PARAMETER Clean
    Clean destination directory / pycache before deployment.

.PARAMETER Help
    Display this help message.

.EXAMPLE
    .\scripts\distribute.ps1 -Local
    .\distribute.ps1 -Local -Method micromamba
    .\distribute.ps1 -Target nemo@192.168.1.38 -SkipDeps -Restart
    .\distribute.ps1 -DryRun -Target nemo@192.168.1.38
#>

[CmdletBinding()]
param(
    [Parameter()]
    [switch]$Local,

    [Parameter(Position=0)]
    [string]$Target,

    [Parameter()]
    [ValidateSet("auto", "micromamba", "venv")]
    [string]$Method = "auto",

    [Parameter()]
    [string]$Dest,

    [Parameter()]
    [switch]$DryRun,

    [Parameter()]
    [switch]$SkipDeps,

    [Parameter()]
    [switch]$SkipHardwareFixes,

    [Parameter()]
    [switch]$SkipService,

    [Alias("Start")]
    [Parameter()]
    [switch]$Restart,

    [Parameter()]
    [int]$Port = 0,

    [Alias("i")]
    [Parameter()]
    [string]$IdentityFile,

    [Parameter()]
    [switch]$Clean,

    [Alias("h")]
    [Parameter()]
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Detailed
    exit 0
}

# Auto-default to -Local if no target provided
if (-not $Local -and [string]::IsNullOrWhiteSpace($Target)) {
    Write-Host "No remote target specified. Defaulting to local system deployment (-Local)." -ForegroundColor Yellow
    $Local = $true
}

# Cross-shell delegation: detect if running locally under Linux/POSIX in PowerShell Core (pwsh)
$IsHostWindows = $true
if ($PSVersionTable.PSEdition -eq "Core") {
    $IsHostWindows = [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)
}

if ($Local -and -not $IsHostWindows) {
    Write-Host "Detected execution on local Linux/POSIX system from PowerShell ($PSVersionTable.PSEdition)." -ForegroundColor Yellow
    Write-Host "Delegating execution to native Bash distributor (scripts/distribute.sh)...`n" -ForegroundColor White
    $BashArgs = @("--local", "--method", $Method)
    if (-not [string]::IsNullOrWhiteSpace($Dest)) { $BashArgs += @("--dest", $Dest) }
    if ($DryRun) { $BashArgs += "--dry-run" }
    if ($SkipDeps) { $BashArgs += "--skip-deps" }
    if ($SkipHardwareFixes) { $BashArgs += "--skip-hardware-fixes" }
    if ($SkipService) { $BashArgs += "--skip-service" }
    if ($Restart) { $BashArgs += "--restart" }
    if ($Clean) { $BashArgs += "--clean" }
    & bash "$ScriptDir/distribute.sh" @BashArgs
    exit $LASTEXITCODE
}

Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host " NemoHeadUnit Universal PowerShell Distributor" -ForegroundColor Cyan
Write-Host " Date   : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host " Target : $(if ($Local) { "Local Machine ($env:COMPUTERNAME)" } else { $Target })" -ForegroundColor Cyan
Write-Host " Method : $Method" -ForegroundColor Cyan
Write-Host " Mode   : $(if ($DryRun) { 'DRY-RUN (Probe only)' } else { 'Full Deployment' })" -ForegroundColor Cyan
Write-Host " Flags  : SkipDeps=$SkipDeps | SkipService=$SkipService | SkipFixes=$SkipHardwareFixes | Restart=$Restart" -ForegroundColor Cyan
Write-Host "==============================================================`n" -ForegroundColor Cyan

# -----------------------------------------------------------------------------
# Local Windows Deployment Pipeline
# -----------------------------------------------------------------------------
if ($Local) {
    Write-Host "[1/4] Inspecting Local Machine Environment and Hardware..." -ForegroundColor Green
    
    $OSName = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption
    $Arch = $env:PROCESSOR_ARCHITECTURE
    $GPUs = (Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name) -join ", "
    
    if ([string]::IsNullOrWhiteSpace($Dest)) {
        $Dest = "$env:LOCALAPPDATA\NemoHeadUnit-Wireless"
    }

    Write-Host "  OS Version   : $OSName" -ForegroundColor White
    Write-Host "  Architecture : $Arch" -ForegroundColor White
    Write-Host "  Detected GPU : $GPUs" -ForegroundColor White
    Write-Host "  Target Path  : $Dest" -ForegroundColor White

    if ($DryRun) {
        Write-Host "`n[Dry-Run] Target inspection completed successfully. No files copied, no packages modified." -ForegroundColor Green
        exit 0
    }

    Write-Host "`n[2/4] Verifying Application Files and Directory..." -ForegroundColor Green
    if ($RepoRoot -ne $Dest) {
        if (-not (Test-Path $Dest)) {
            New-Item -ItemType Directory -Path $Dest -Force | Out-Null
        } elseif ($Clean) {
            Write-Host "  [Clean] Purging previous files in $Dest..." -ForegroundColor Yellow
            Get-ChildItem -Path $Dest -Exclude ".git" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
        Write-Host "  Synchronizing repository to $Dest..." -ForegroundColor White
        Copy-Item -Path "$RepoRoot\*" -Destination $Dest -Recurse -Force -Exclude ".git", "__pycache__", "env", "*.pyc", "build", "dist"
    } else {
        Write-Host "  Running directly in workspace ($RepoRoot)." -ForegroundColor White
    }

    Write-Host "`n[3/4] Checking Python and Micromamba/Conda Environment..." -ForegroundColor Green
    if (-not $SkipDeps) {
        $HasMicromamba = (Get-Command micromamba -ErrorAction SilentlyContinue) -ne $null
        $HasConda = (Get-Command conda -ErrorAction SilentlyContinue) -ne $null
        $HasPython = (Get-Command python -ErrorAction SilentlyContinue) -ne $null

        if ($Method -eq "micromamba" -or ($Method -eq "auto" -and $HasMicromamba)) {
            Write-Host "  Using Micromamba environment engine." -ForegroundColor Green
            if (Test-Path "$Dest\environment.windows.yml") {
                micromamba install -y -n NemoHeadUnit-Wireless -f "$Dest\environment.windows.yml"
            }
        } elseif ($HasConda) {
            Write-Host "  Using Conda environment engine." -ForegroundColor Green
            if (Test-Path "$Dest\environment.windows.yml") {
                conda env update -n NemoHeadUnit-Wireless -f "$Dest\environment.windows.yml" --prune
            }
        } elseif ($HasPython) {
            Write-Host "  Using standard Python venv engine." -ForegroundColor Green
            if (-not (Test-Path "$Dest\env")) {
                python -m venv "$Dest\env"
            }
            & "$Dest\env\Scripts\python.exe" -m pip install --upgrade pip
            if (Test-Path "$Dest\packaging\requirements.txt") {
                & "$Dest\env\Scripts\python.exe" -m pip install -r "$Dest\packaging\requirements.txt"
            }
        } else {
            Write-Host "  Warning: No Python or Conda/Micromamba found in PATH!" -ForegroundColor Yellow
            Write-Host "  Please install Micromamba or Python 3.11+ to run NemoHeadUnit." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [SkipDeps] Skipping dependency installations." -ForegroundColor Yellow
    }

    Write-Host "`n[4/4] Configuring Services and Diagnostics..." -ForegroundColor Green
    if (-not $SkipService) {
        try {
            $WshShell = New-Object -ComObject WScript.Shell
            $DesktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
            $ShortcutPath = Join-Path $DesktopPath "NemoHeadUnit.lnk"
            $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
            if (Test-Path "$Dest\scripts\launch_qt_kiosk.bat") {
                $Shortcut.TargetPath = "$Dest\scripts\launch_qt_kiosk.bat"
            } elseif (Test-Path "$Dest\scripts\launch_kiosk.bat") {
                $Shortcut.TargetPath = "$Dest\scripts\launch_kiosk.bat"
            }
            $Shortcut.WorkingDirectory = $Dest
            $Shortcut.Description = "NemoHeadUnit - Wireless Android Auto"
            $Shortcut.Save()
            Write-Host "  Created Desktop shortcut: $ShortcutPath" -ForegroundColor Green
        } catch {
            Write-Host "  Notice: Could not create desktop shortcut ($_)" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "  [SkipService] Skipping desktop shortcut creation." -ForegroundColor Yellow
    }

    if (Test-Path "$Dest\scripts\hardware_tests\verify_windows_qt6.py") {
        if ((Get-Command micromamba -ErrorAction SilentlyContinue) -ne $null) {
            micromamba run -n NemoHeadUnit-Wireless python "$Dest\scripts\hardware_tests\verify_windows_qt6.py"
        } elseif ((Get-Command python -ErrorAction SilentlyContinue) -ne $null) {
            python "$Dest\scripts\hardware_tests\verify_windows_qt6.py"
        }
    }

    if ($Restart) {
        Write-Host "`n  [Restart] Launching NemoHeadUnit application..." -ForegroundColor Green
        if (Test-Path "$Dest\scripts\launch_qt_kiosk.bat") {
            Start-Process -FilePath "$Dest\scripts\launch_qt_kiosk.bat" -WorkingDirectory $Dest
        }
    }

    Write-Host "`n==============================================================" -ForegroundColor Green
    Write-Host " Local Distribution Complete!" -ForegroundColor Green
    Write-Host "==============================================================" -ForegroundColor Green
    Write-Host "To launch NemoHeadUnit:" -ForegroundColor White
    Write-Host "  Micromamba: micromamba run -n NemoHeadUnit-Wireless python backend/main.py" -ForegroundColor Cyan
    Write-Host "  Or Launch:  scripts\launch_qt_kiosk.bat`n" -ForegroundColor Cyan
    return
}

# -----------------------------------------------------------------------------
# Remote Target Deployment over SSH (Cross-Platform: Windows -> Linux or Windows -> Windows)
# -----------------------------------------------------------------------------
$SshArgs = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new")
if ($Port -gt 0) {
    $SshArgs += @("-p", $Port)
}
if (-not [string]::IsNullOrWhiteSpace($IdentityFile)) {
    $SshArgs += @("-i", $IdentityFile)
}

Write-Host "[1/4] Testing Remote SSH Connection to $Target..." -ForegroundColor Green
try {
    ssh @SshArgs $Target "exit 0" | Out-Null
    Write-Host "  Connected successfully to $Target." -ForegroundColor Green
} catch {
    Write-Host "  Could not connect via passwordless SSH. Please ensure SSH keys or credentials are valid." -ForegroundColor Yellow
}

Write-Host "`n[2/4] Probing Remote System via SSH..." -ForegroundColor Green
$WinCheck = ssh @SshArgs $Target 'powershell.exe -NoProfile -Command "Write-Output IS_WINDOWS"' 2>$null
$IsRemoteLinux = $true
if ($WinCheck -match "IS_WINDOWS") {
    $IsRemoteLinux = $false
}

if ($IsRemoteLinux) {
    $RemoteProbe = ssh @SshArgs $Target '
        echo OS_NAME=$(uname -s)
        echo ARCH=$(uname -m)
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            echo PRETTY_NAME="$PRETTY_NAME"
            echo DISTRO_ID="$ID"
        fi
    ' 2>$null
} else {
    $RemoteProbe = ssh @SshArgs $Target 'powershell.exe -NoProfile -Command "$ProgressPreference = ''SilentlyContinue''; Write-Output OS_NAME=Windows; Write-Output ARCH=$env:PROCESSOR_ARCHITECTURE; Write-Output DISTRO_ID=windows"' 2>$null
}
$RemoteProbe -split "`n" | ForEach-Object {
    if ($_ -match "=") {
        Write-Host "    $_" -ForegroundColor White
    }
}

if ($IsRemoteLinux) {
    # Normalize path if default Windows path or empty
    if ([string]::IsNullOrWhiteSpace($Dest) -or $Dest -match "^[a-zA-Z]:") {
        $Dest = "/opt/nemo-headunit"
    }

    if ($DryRun) {
        Write-Host "`n[Dry-Run] Target inspection completed successfully. No payload transferred." -ForegroundColor Green
        exit 0
    }

    Write-Host "`n[3/4] Streaming Repository to Remote Linux Host ($Target`:$Dest)..." -ForegroundColor Green
    ssh @SshArgs $Target "sudo mkdir -p $Dest && sudo chown -R `$(id -un):`$(id -gn) $Dest"
    if ($Clean) {
        Write-Host "  [Clean] Cleaning target directory on remote host..." -ForegroundColor Yellow
        ssh @SshArgs $Target "find $Dest -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} + 2>/dev/null || true"
    }
    
    # Use native tar.exe (Windows 10/11) or tar
    tar.exe -cz -C "$RepoRoot" --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='env' --exclude='build' --exclude='dist' . | ssh @SshArgs $Target "tar -xz -C $Dest"
    Write-Host "  Payload transferred successfully." -ForegroundColor Green

    Write-Host "`n[4/4] Executing Remote Distribution Pipeline on Linux Host with forwarded flags..." -ForegroundColor Green
    $RemoteBashArgs = "--local --method $Method --dest `"$Dest`""
    if ($SkipDeps) { $RemoteBashArgs += " --skip-deps" }
    if ($SkipHardwareFixes) { $RemoteBashArgs += " --skip-hardware-fixes" }
    if ($SkipService) { $RemoteBashArgs += " --skip-service" }
    if ($Restart) { $RemoteBashArgs += " --restart" }
    ssh @SshArgs -t $Target "cd $Dest && bash scripts/distribute.sh $RemoteBashArgs"
} else {
    # Target is Remote Windows
    if ([string]::IsNullOrWhiteSpace($Dest) -or $Dest.StartsWith("/")) {
        $Dest = "C:\NemoHeadUnit-Wireless"
    }

    if ($DryRun) {
        Write-Host "`n[Dry-Run] Target inspection completed successfully. No payload transferred." -ForegroundColor Green
        exit 0
    }

    Write-Host "`n[3/4] Streaming Repository to Remote Windows Host ($Target`:$Dest)..." -ForegroundColor Green
    $WinInitPS = "if (-not (Test-Path '$Dest')) { New-Item -ItemType Directory -Path '$Dest' -Force | Out-Null }"
    if ($Clean) {
        $WinInitPS += "; Remove-Item -Path '$Dest\*' -Recurse -Force -Exclude '.git' -ErrorAction SilentlyContinue"
    }
    ssh @SshArgs $Target "powershell.exe -NoProfile -Command `"$WinInitPS`""
    tar.exe -cz -C "$RepoRoot" --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='env' --exclude='build' --exclude='dist' . | ssh @SshArgs $Target "tar.exe -xz -C `"$Dest`""
    Write-Host "  Payload transferred successfully." -ForegroundColor Green

    Write-Host "`n[4/4] Executing Remote PowerShell Distributor on Windows Host with forwarded flags..." -ForegroundColor Green
    $RemotePSArgs = "-Local -Dest `"$Dest`" -Method $Method"
    if ($SkipDeps) { $RemotePSArgs += " -SkipDeps" }
    if ($SkipHardwareFixes) { $RemotePSArgs += " -SkipHardwareFixes" }
    if ($SkipService) { $RemotePSArgs += " -SkipService" }
    if ($Restart) { $RemotePSArgs += " -Restart" }
    if ($Clean) { $RemotePSArgs += " -Clean" }
    ssh @SshArgs $Target "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Dest\scripts\distribute.ps1`" $RemotePSArgs"
}
