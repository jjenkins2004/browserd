# Install browserd on Windows, or update it in place, from PowerShell:
#
#     irm https://raw.githubusercontent.com/jjenkins2004/browserd/main/install.ps1 | iex
#
# It puts the release named by VERSION on main (or $env:BROWSERD_REF: a tag, a branch or a commit) in
# %LOCALAPPDATA%\Programs\browserd\<version>, runs npm ci there for chrome-devtools-mcp, and puts browserd.cmd in
# %LOCALAPPDATA%\Programs\browserd\bin, which it adds to the user's PATH. Each version has a folder of its own: Windows
# will not replace a folder a running server works in. A server already running is restarted on the new version, and
# every Chrome and session kept. Its records stay in %LOCALAPPDATA%\browserd, whichever version runs.
#
#     $env:BROWSERD_REF       what to install: v0.2.0, main, ...; the latest release otherwise
#     $env:BROWSERD_PYTHON    the Python 3.10 or later to run it with, as a command line; py -3 otherwise
#     $env:BROWSERD_ARCHIVE   a .zip of browserd to install instead of downloading one, as git archive makes
#     $env:BROWSERD_NO_PATH   set to leave the user's PATH alone
#
# To uninstall: browserd stop, then remove %LOCALAPPDATA%\Programs\browserd and its bin folder from the user's PATH,
# and %LOCALAPPDATA%\browserd for its records too.

& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'  # Windows PowerShell's progress bar slows a download many times over
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $Repo = 'jjenkins2004/browserd'
    $Base = Join-Path $env:LOCALAPPDATA 'Programs\browserd'
    $BinDir = Join-Path $Base 'bin'
    $Shim = Join-Path $BinDir 'browserd.cmd'

    function Fail($message) { throw "browserd: $message" }
    # A program's exit code in $LASTEXITCODE, and its stderr never an error of this script's, as Windows PowerShell
    # makes it under 'Stop' once redirected. quiet drops stderr.
    function Run([string[]]$command, [switch]$quiet) {
        $ErrorActionPreference = 'Continue'
        $exe, $rest = $command[0], @($command | Select-Object -Skip 1)
        if ($quiet) { & $exe @rest 2>$null } else { & $exe @rest }
    }
    # Remove a folder, and say whether it is gone: rd, through \\?\, for node_modules' paths past 260 characters,
    # which Windows PowerShell's Remove-Item cannot reach. One a running server works in stays.
    function Remove-Tree([string]$path) {
        if (Test-Path -LiteralPath $path) { Run 'cmd.exe', '/d', '/c', "rd /s /q `"\\?\$path`"" -quiet | Out-Null }
        -not (Test-Path -LiteralPath $path)
    }

    # The Python browserd.cmd will run: BROWSERD_PYTHON, py -3, or python, in that order.
    if ($env:BROWSERD_PYTHON) {
        $py = $env:BROWSERD_PYTHON -split ' '
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $py = @('py', '-3')
    } else {
        $py = @('python')
    }
    $said = Run ($py + '-c' + "import sys, sysconfig; print('%d.%d %s' % (sys.version_info[:2] + (sysconfig.get_platform(),)))") -quiet
    if ($LASTEXITCODE -ne 0 -or -not $said) {
        Fail "Python is not installed; browserd needs Python 3.10 or later from python.org or the Microsoft Store (winget install Python.Python.3.13)"
    }
    $pyVersion, $pyPlatform = "$said".Trim() -split ' '
    if ([version]$pyVersion -lt [version]'3.10') { Fail "Python $pyVersion is too old; browserd needs 3.10 or later" }
    if ($pyPlatform -like 'mingw*') { Fail "$($py -join ' ') is MSYS2's Python; browserd needs the one from python.org or the Microsoft Store" }

    if (-not (Get-Command node.exe -ErrorAction SilentlyContinue) -or -not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        Fail "node and npm are not installed; browserd needs Node 20.19 or later (winget install OpenJS.NodeJS.LTS)"
    }
    Run 'node.exe', '-e', "const [a, b] = process.versions.node.split('.').map(Number); process.exit(a > 20 || (a === 20 && b >= 19) ? 0 : 1)"
    if ($LASTEXITCODE -ne 0) { Fail "node $(Run 'node.exe', '--version') is too old; browserd needs 20.19 or later" }

    $chromes = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) | Where-Object { $_ } |
        ForEach-Object { Join-Path $_ 'Google\Chrome\Application\chrome.exe' }
    if (-not ($chromes | Where-Object { Test-Path $_ })) {
        Write-Host 'note: Google Chrome is not installed; browserd needs it (winget install Google.Chrome)'
    }

    $ref = $env:BROWSERD_REF
    if ($env:BROWSERD_ARCHIVE) {
        $ref = 'local'
    } elseif (-not $ref) {
        $latest = Invoke-RestMethod -UseBasicParsing "https://raw.githubusercontent.com/$Repo/main/VERSION"
        $ref = 'v' + "$latest".Trim()
    }

    New-Item -ItemType Directory -Force $BinDir | Out-Null
    $staging = Join-Path $Base (".staging-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory $staging | Out-Null
    try {
        $zip = Join-Path $staging 'browserd.zip'
        if ($env:BROWSERD_ARCHIVE) {
            Copy-Item $env:BROWSERD_ARCHIVE $zip
        } else {
            Write-Host "downloading browserd $ref"
            try {
                Invoke-WebRequest -UseBasicParsing "https://github.com/$Repo/archive/$ref.zip" -OutFile $zip
            } catch {
                Fail "could not download $ref from github.com/$Repo ($($_.Exception.Message))"
            }
        }
        Expand-Archive $zip -DestinationPath $staging
        Remove-Item $zip
        $source = @(Get-ChildItem $staging -Directory)[0].FullName
        $version = (Get-Content (Join-Path $source 'VERSION') -Raw).Trim()
        if ($ref -notmatch '^v\d') { $version = "$version-$($ref -replace '[^\w.-]', '-')" }

        Write-Host 'installing chrome-devtools-mcp'
        Push-Location $source
        try {
            Run 'npm.cmd', 'ci', '--omit=dev', '--no-audit', '--no-fund', '--update-notifier=false', '--loglevel=error'
            if ($LASTEXITCODE -ne 0) { Fail "npm ci failed in $source" }
        } finally {
            Pop-Location
        }

        # Asked of the version already installed, so its server can be restarted on this one.
        $running = $false
        if (Test-Path $Shim) {
            $running = "$(Run $Shim, 'status' -quiet)" -like 'running*'
        }

        $app = Join-Path $Base $version
        if (Test-Path $app) {
            if (-not (Remove-Tree $app)) { $app = "$app-$(Get-Date -Format yyyyMMddHHmmss)" }
        }
        Move-Item $source $app
    } finally {
        Remove-Tree $staging | Out-Null
    }

    # The shim on the PATH names this version's browserd.cmd; ANSI, as cmd reads a batch file.
    $shimText = "@echo off`r`nrem browserd $version, put here by install.ps1`r`ncall `"$app\browserd.cmd`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    [IO.File]::WriteAllText($Shim, $shimText, [Text.Encoding]::Default)
    Write-Host "installed $(Run $Shim, 'version')"

    if ($running) { Run $Shim, 'restart' }

    # Every older version whose server has stopped; one still in use stays until the next install.
    Get-ChildItem $Base -Directory | Where-Object { $_.Name -ne 'bin' -and $_.FullName -ne $app } | ForEach-Object {
        Remove-Tree $_.FullName | Out-Null
    }

    # The user's PATH, read and written unexpanded so its %VARIABLES% stay as they are.
    $key = Get-Item 'HKCU:\Environment'
    $userPath = $key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
    if (-not $env:BROWSERD_NO_PATH -and -not (($userPath -split ';') -contains $BinDir)) {
        $newPath = (($userPath.TrimEnd(';'), $BinDir) | Where-Object { $_ }) -join ';'
        Set-ItemProperty 'HKCU:\Environment' -Name Path -Value $newPath -Type ExpandString
        # Setting a variable through .NET tells Explorer the environment changed, so new terminals see the PATH.
        [Environment]::SetEnvironmentVariable('BROWSERD_INSTALLING', '1', 'User')
        [Environment]::SetEnvironmentVariable('BROWSERD_INSTALLING', $null, 'User')
        Write-Host "added $BinDir to your PATH; terminals opened from now on have it (restart VS Code for its terminals)"
    }
    if (-not (($env:Path -split ';') -contains $BinDir)) { $env:Path = "$env:Path;$BinDir" }

    Write-Host ''
    Write-Host 'next:'
    Write-Host '  browserd start'
    Write-Host '  claude mcp add -s user --transport http browserd http://127.0.0.1:9230/mcp'
}
