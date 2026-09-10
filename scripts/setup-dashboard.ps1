<#
.SYNOPSIS
  Sets up the Jarvis dashboard inside an Obsidian vault on Windows and
  configures the Command and Agenda widgets.

.DESCRIPTION
  PowerShell port of scripts/setup-dashboard.sh, for Windows without Git Bash
  or WSL. Same behaviour, same guarantees:

    - Safe to re-run. Never overwrites an existing config, never stacks
      duplicate layout tiles, never replaces a token that already works.
    - The GitHub token is read interactively and NEVER taken as a parameter.
      A parameter lands in PSReadLine history (%APPDATA%\Microsoft\Windows\
      PowerShell\PSReadLine\ConsoleHost_history.txt) in plain text, which is
      exactly where a token should not be.
    - config.local.json is locked down with icacls so only you can read it.
    - The token is verified against the real repo BEFORE the script claims
      success, and a failure says which value to correct.

  Uses only built-in cmdlets -- no python3, no curl, no Git Bash. Git is the
  one external requirement.

.PARAMETER Vault
  Your Obsidian vault: the folder that contains .obsidian\

.PARAMETER OrchestratorRepo
  owner/repo holding facts/command.json and facts/agenda.json.
  Defaults to 0x5a65726f4b65776c/-jarvis-orchestrator

.PARAMETER SkipTokenCheck
  Configure without a live GitHub call (offline setup).

.EXAMPLE
  .\scripts\setup-dashboard.ps1 -Vault "$HOME\Documents\MyVault"

.EXAMPLE
  .\scripts\setup-dashboard.ps1 -Vault D:\Obsidian\Nick -OrchestratorRepo 0x5a65726f4b65776c/-jarvis-orchestrator
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Vault,

    [Parameter(Position = 1)]
    [string]$OrchestratorRepo = '0x5a65726f4b65776c/-jarvis-orchestrator',

    [switch]$SkipTokenCheck
)

$ErrorActionPreference = 'Stop'

$DashRepo = 'https://github.com/0x5a65726f4b65776c/jarvis-dashboard.git'
$DestName = 'Jarvis Dashboard'

function Write-Step { param([string]$m) Write-Host "`n== $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host $m -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host $m -ForegroundColor Yellow }
function Stop-Fail  { param([string]$m) Write-Host "FAILED: $m" -ForegroundColor Red; exit 1 }

# ── Prerequisites ──────────────────────────────────────────────────────────
Write-Step 'Checking prerequisites'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Stop-Fail 'git not found. Install Git for Windows: https://git-scm.com/download/win'
}
Write-Ok "git present ($((git --version)))"

# Resolve ~ and relative paths the way a user expects.
$Vault = $Vault -replace '^~', $HOME
try   { $Vault = (Resolve-Path -LiteralPath $Vault).Path }
catch { Stop-Fail "Vault not found: $Vault" }

if (-not (Test-Path -LiteralPath (Join-Path $Vault '.obsidian'))) {
    Write-Warn "No .obsidian\ in $Vault -- is that really your vault root?"
    Write-Warn 'Continuing anyway; Obsidian creates .obsidian on first open.'
}
$Dest = Join-Path $Vault $DestName

# ── Clone or update ────────────────────────────────────────────────────────
Write-Step 'Installing the dashboard into the vault'
if (Test-Path -LiteralPath (Join-Path $Dest '.git')) {
    Write-Ok "Already present at: $Dest"
    Write-Host '  Pulling latest...'
    git -C $Dest pull --ff-only 2>&1 | ForEach-Object { Write-Host "  $_" }
} else {
    git clone --depth 1 $DashRepo $Dest 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) { Stop-Fail "git clone failed (exit $LASTEXITCODE)" }
    Write-Ok "Cloned to: $Dest"
}

$SrcDir = Join-Path $Dest 'src'
$Cfg    = Join-Path $SrcDir 'config\config.json'
$LCfg   = Join-Path $SrcDir 'config\config.local.json'

# ── Config files ───────────────────────────────────────────────────────────
Write-Step 'Creating config files'
# config.json -> config.example.json, config.local.json -> config.local.example.json
foreach ($real in @($Cfg, $LCfg)) {
    $example = $real -replace '\.json$', '.example.json'
    if (Test-Path -LiteralPath $real) {
        Write-Ok "Kept existing $(Split-Path $real -Leaf)"
    } elseif (Test-Path -LiteralPath $example) {
        Copy-Item -LiteralPath $example -Destination $real
        Write-Ok "Created $(Split-Path $real -Leaf) from the example"
    } else {
        Stop-Fail "Neither $real nor $example exists -- is the clone complete?"
    }
}

# ── Point the widgets at the orchestrator repo ─────────────────────────────
Write-Step "Pointing the Command and Agenda widgets at $OrchestratorRepo"

$cfgObj = Get-Content -LiteralPath $Cfg -Raw | ConvertFrom-Json

if (-not $cfgObj.PSObject.Properties['widgets']) {
    $cfgObj | Add-Member -NotePropertyName widgets -NotePropertyValue ([pscustomobject]@{})
}
if (-not $cfgObj.widgets.PSObject.Properties['command']) {
    $cfgObj.widgets | Add-Member -NotePropertyName command -NotePropertyValue ([pscustomobject]@{})
}
if ($cfgObj.widgets.command.PSObject.Properties['repo']) {
    $cfgObj.widgets.command.repo = $OrchestratorRepo
} else {
    $cfgObj.widgets.command | Add-Member -NotePropertyName repo -NotePropertyValue $OrchestratorRepo
}

# Agenda deliberately gets no repo, ref or token of its own: it falls back to
# the Command widget's, because both files live in the same orchestrator repo.
# A second copy of the same settings is a second thing to get out of sync, and
# a second PAT is a setup step that exists only to be got wrong.
if (-not $cfgObj.widgets.PSObject.Properties['agenda']) {
    $cfgObj.widgets | Add-Member -NotePropertyName agenda -NotePropertyValue ([pscustomobject]@{
        path      = 'facts/agenda.json'
        refreshMs = 900000
    })
}

# Layout: add tiles only if absent. Re-running must not stack duplicates.
if (-not $cfgObj.PSObject.Properties['layout'] -or $null -eq $cfgObj.layout) {
    $cfgObj | Add-Member -NotePropertyName layout -NotePropertyValue @() -Force
}
$layout = [System.Collections.ArrayList]@($cfgObj.layout)

function Get-TileIndex {
    param([System.Collections.ArrayList]$List, [string]$Type)
    for ($i = 0; $i -lt $List.Count; $i++) {
        if ($List[$i] -and $List[$i].PSObject.Properties['type'] -and $List[$i].type -eq $Type) { return $i }
    }
    return -1
}

if ((Get-TileIndex $layout 'command') -lt 0) {
    $at = (Get-TileIndex $layout 'header') + 1
    $layout.Insert($at, ([pscustomobject]@{ type = 'command' })) | Out-Null
    Write-Host '  layout: inserted the command tile after the header'
} else {
    Write-Host '  layout: command tile already present'
}

# Agenda sits directly under Command: what needs attention outranks what is
# merely scheduled.
if ((Get-TileIndex $layout 'agenda') -lt 0) {
    $at = (Get-TileIndex $layout 'command') + 1
    $layout.Insert($at, ([pscustomobject]@{ type = 'agenda' })) | Out-Null
    Write-Host '  layout: inserted the agenda tile after command'
} else {
    Write-Host '  layout: agenda tile already present'
}
$cfgObj.layout = @($layout)

# -Depth matters: PowerShell's default of 2 silently truncates nested config
# into the string "System.Object[]", which corrupts the file.
$cfgObj | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $Cfg -Encoding UTF8
Write-Host "  widgets.command.repo = $OrchestratorRepo"
Write-Host '  widgets.agenda       = inherits repo/ref/token from command'

# ── Token ──────────────────────────────────────────────────────────────────
Write-Step 'GitHub token'
$lcfgObj = Get-Content -LiteralPath $LCfg -Raw | ConvertFrom-Json

$existing = ''
if ($lcfgObj.PSObject.Properties['command'] -and
    $lcfgObj.command.PSObject.Properties['githubToken']) {
    $t = [string]$lcfgObj.command.githubToken
    if ($t -and -not $t.StartsWith('YOUR_')) { $existing = $t }
}

if ($existing) {
    Write-Ok 'A token is already configured -- leaving it alone'
    $Token = $existing
} else {
    Write-Host "  Needs a fine-grained PAT with read-only Contents access on $OrchestratorRepo."
    Write-Host '  Create at: https://github.com/settings/personal-access-tokens'
    # Read-Host -AsSecureString keeps the token off the screen and out of
    # PSReadLine history. Never accept it as a parameter.
    $secure = Read-Host -Prompt '  Paste token (input hidden)' -AsSecureString
    $Token  = [System.Net.NetworkCredential]::new('', $secure).Password
    if (-not $Token) { Stop-Fail 'No token entered.' }

    if (-not $lcfgObj.PSObject.Properties['command']) {
        $lcfgObj | Add-Member -NotePropertyName command -NotePropertyValue ([pscustomobject]@{})
    }
    if ($lcfgObj.command.PSObject.Properties['githubToken']) {
        $lcfgObj.command.githubToken = $Token
    } else {
        $lcfgObj.command | Add-Member -NotePropertyName githubToken -NotePropertyValue $Token
    }
    $lcfgObj | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $LCfg -Encoding UTF8
    Write-Ok 'Token written to config.local.json'
}

# Windows has no chmod 600. The equivalent is: disable inheritance, drop every
# inherited ACE, and grant only the current user.
try {
    icacls $LCfg /inheritance:r /grant:r "$($env:USERNAME):(F)" | Out-Null
    Write-Ok 'config.local.json locked to your user account only'
} catch {
    Write-Warn "Could not tighten permissions on $LCfg -- check them by hand."
}

# ── Verify ─────────────────────────────────────────────────────────────────
Write-Step "Verifying the token against $OrchestratorRepo"
if ($SkipTokenCheck) {
    Write-Warn '-SkipTokenCheck given -- skipping the live check'
} else {
    $headers = @{
        'Accept'               = 'application/vnd.github.raw+json'
        'Authorization'        = "Bearer $Token"
        'X-GitHub-Api-Version' = '2022-11-28'
        'User-Agent'           = 'jarvis-dashboard-setup'
    }

    function Invoke-Probe {
        param([string]$File)
        $uri = "https://api.github.com/repos/$OrchestratorRepo/contents/facts/$File`?ref=main"
        try {
            $r = Invoke-WebRequest -Uri $uri -Headers $headers -UseBasicParsing -ErrorAction Stop
            return @{ Code = [int]$r.StatusCode; Body = $r.Content }
        } catch {
            $code = 0
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
            return @{ Code = $code; Body = '' }
        }
    }

    # config.json was written before this check ran, so a failure here leaves it
    # pointing at a repo that did not work. Say so rather than leaving the user
    # to discover it -- re-running with the right value fixes it.
    function Stop-Verify {
        param([string]$m)
        Write-Host "FAILED: $m" -ForegroundColor Red
        Write-Warn "config.json now points at $OrchestratorRepo. Re-run with the correct repo to correct it:"
        Write-Warn "  .\scripts\setup-dashboard.ps1 -Vault `"$Vault`" -OrchestratorRepo <owner>/<repo>"
        exit 1
    }

    $cmdProbe = Invoke-Probe 'command.json'
    switch ($cmdProbe.Code) {
        200 {
            try {
                $d = $cmdProbe.Body | ConvertFrom-Json
                $n = @($d.items).Count
                Write-Ok "OK -- command.json reachable: $n item(s), generated_at $($d.generated_at)"
            } catch { Write-Ok 'OK -- command.json reachable (unparseable body)' }
        }
        401 { Stop-Verify "GitHub returned 401. The token is invalid or expired." }
        403 { Stop-Verify "GitHub returned 403. The token lacks Contents read on $OrchestratorRepo." }
        404 { Stop-Verify "GitHub returned 404. Either $OrchestratorRepo/facts/command.json does not exist, or the token cannot see this private repo." }
        0   { Stop-Verify 'Could not reach GitHub. Check your network.' }
        default { Stop-Verify "GitHub returned $($cmdProbe.Code)." }
    }

    # agenda.json is probed separately and is NOT fatal. The Agenda panel is
    # useful the moment the rollup first writes the file; a vault set up before
    # that happens should still finish successfully rather than fail on a file
    # that is legitimately not there yet.
    $agProbe = Invoke-Probe 'agenda.json'
    switch ($agProbe.Code) {
        200 {
            try {
                $d = $agProbe.Body | ConvertFrom-Json
                Write-Ok "OK -- agenda.json reachable: $(@($d.events).Count) event(s), $(@($d.todos).Count) todo(s), for $($d.for_date)"
            } catch { Write-Ok 'OK -- agenda.json reachable (unparseable body)' }
        }
        404 {
            Write-Warn 'agenda.json not found yet. The Agenda panel will say so until the daily rollup first writes it.'
            Write-Warn '  That step lives in the rollup ROUTINE PROMPT, not in repo code --'
            Write-Warn '  see -jarvis-orchestrator/doctrine/routine_prompts/jarvis-rollup.txt.'
        }
        default {
            Write-Warn "agenda.json probe returned $($agProbe.Code). Command still works; the Agenda panel will show the error."
        }
    }
}

# ── Dataview ───────────────────────────────────────────────────────────────
Write-Step 'Checking the Dataview plugin'
$dvData = Join-Path $Vault '.obsidian\plugins\dataview\data.json'
if (Test-Path -LiteralPath $dvData) {
    try {
        $dv = Get-Content -LiteralPath $dvData -Raw | ConvertFrom-Json
        if ($dv.enableDataviewJs -eq $true) {
            Write-Ok 'Dataview installed, JavaScript Queries enabled'
        } else {
            Write-Warn 'Dataview is installed but JavaScript Queries are OFF.'
            Write-Warn '  Settings > Community plugins > Dataview > Enable JavaScript Queries'
        }
    } catch {
        Write-Warn 'Dataview data.json is unreadable; check the plugin settings by hand.'
    }
} else {
    Write-Warn 'Dataview not found. Install it (Community plugins > Browse > Dataview),'
    Write-Warn '  then enable Settings > Dataview > Enable JavaScript Queries.'
}

# ── Done ───────────────────────────────────────────────────────────────────
Write-Step 'Done'
Write-Host 'Open this note in Obsidian:'
Write-Host ''
Write-Host "  $DestName\Jarvis Dashboard.md"
Write-Host ''
Write-Host 'Command sits directly under the header, with Agenda beneath it. On mobile both'
Write-Host 'appear above the voice widget, automatically, because widgets.command.repo is'
Write-Host 'now set -- Agenda inherits the repo, ref and token from Command, so there is'
Write-Host 'nothing further to configure for it.'
Write-Host ''
Write-Host 'Still worth setting by hand in config.json if you want the session widgets:'
Write-Host '  projects.mode / projects.rootPath'
