param([Parameter(Mandatory=$true)][string]$PackageDirectory)
$ErrorActionPreference = 'Stop'
$PackageDirectory = (Resolve-Path $PackageDirectory).Path
$nativeDirectory = Join-Path $env:RUNNER_TEMP 'hoa-native-smoke'
Expand-Archive (Join-Path $PackageDirectory 'game-launcher.zip') $nativeDirectory
$env:HOA_NATIVE_CONFIG = Join-Path $env:RUNNER_TEMP 'hoa-native-settings'

function Test-Window([string]$Executable, [string]$ProcessName) {
    $found = $false
    try {
        Start-Process -FilePath $Executable | Out-Null
        # PyInstaller's one-file bootstrapper starts a child with the same name;
        # inspect both rather than assuming the parent owns the window.
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Seconds 1
            $windows = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
                Where-Object { $_.MainWindowTitle -like 'Hordes Forever*' -and $_.Responding })
            if ($windows.Count -gt 0) {
                Write-Output "PASS: packaged application opened a responsive window: $($windows[0].MainWindowTitle)"
                $found = $true
                break
            }
        }
        if (-not $found) {
            $titles = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty MainWindowTitle)
            throw "Packaged application did not open the expected window. Observed titles: $($titles -join '; ')"
        }
    }
    finally {
        Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Stop-Process -Force
    }
}

try {
    Test-Window (Join-Path $PackageDirectory 'HordesForeverSetup.exe') 'HordesForeverSetup'
    Test-Window (Join-Path $nativeDirectory 'forever-launcher.exe') 'forever-launcher'
}
catch {
    Write-Output "::error title=Packaged Windows smoke test::$($_.Exception.Message)"
    throw
}
