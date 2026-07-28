$ErrorActionPreference = "Stop"

$Distro = ""
$NoVenv = $false
$Command = @()
for ($i = 0; $i -lt $args.Count; $i++) {
    $arg = [string]$args[$i]
    if ($arg -eq "-Distro" -or $arg -eq "--distro") {
        $i += 1
        if ($i -ge $args.Count) {
            throw "$arg requires a distro name"
        }
        $Distro = [string]$args[$i]
        continue
    }
    if ($arg -eq "-NoVenv" -or $arg -eq "--no-venv") {
        $NoVenv = $true
        continue
    }
    if ($arg -eq "--") {
        if ($i + 1 -lt $args.Count) {
            $Command = @($args[($i + 1)..($args.Count - 1)])
        }
        break
    }
    $Command = @($args[$i..($args.Count - 1)])
    break
}

function ConvertTo-BashLiteral {
    param([string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$repoPath = ""
if ($repoRoot.Path -match "^([A-Za-z]):\\(.*)$") {
    $drive = $Matches[1].ToLowerInvariant()
    $relative = $Matches[2].Replace("\", "/")
    $repoPath = "/mnt/$drive/$relative"
} else {
    $wslPathArgs = @("wslpath", "-a", "-u", $repoRoot.Path)
    if ($Distro) {
        $repoPath = (& wsl.exe -d $Distro -e @wslPathArgs).Trim()
    } else {
        $repoPath = (& wsl.exe -e @wslPathArgs).Trim()
    }
}

if (-not $repoPath) {
    throw "failed to resolve repository path inside WSL"
}

if ($Command.Count -eq 0) {
    $Command = @("wavebench", "--help")
}

$escapedCommand = ($Command | ForEach-Object { ConvertTo-BashLiteral $_ }) -join " "
$repoLiteral = ConvertTo-BashLiteral $repoPath
$venvLine = ""
if (-not $NoVenv) {
    $venvLine = "if [ ! -f .venv-wsl/bin/activate ]; then echo 'missing .venv-wsl; run: python3 -m venv .venv-wsl && source .venv-wsl/bin/activate && pip install -e ''.[dev]''' >&2; exit 2; fi; . .venv-wsl/bin/activate;"
}

$bashScript = "set -e; cd $repoLiteral; $venvLine exec $escapedCommand"

if ($Distro) {
    & wsl.exe -d $Distro -e bash -lc $bashScript
} else {
    & wsl.exe -e bash -lc $bashScript
}

exit $LASTEXITCODE
