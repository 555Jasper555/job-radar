<#
.SYNOPSIS
    gig-scout daily loop: scan posted-budget work, then have a bounded `claude -p`
    draft one application per new qualifying post. Unattended, hidden window.
.DESCRIPTION
    1. python gigs/scout.py --days N      -> _job-radar-data/gigs/{board.md,new.jsonl}
    2. if new.jsonl has rows: claude -p    -> _job-radar-data/gigs/drafts/<date>-<HHmm>.md
    Jasper reads drafts/, sends what he approves. Nothing here sends anything.
    Markers: gigs/state/RUNNING (PID lock), gigs/state/LAST_RUN (status), logs/ tee.
    Registered as the Windows scheduled task "gig-scout" (see gigs/README.md).
#>
param([int]$Days = 1, [int]$MaxDrafts = 6)
$ErrorActionPreference = "Continue"
$env:CLAUDECODE = $null
try { Remove-Item Env:\CLAUDECODE -ErrorAction SilentlyContinue } catch { }
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"

$gigsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo    = (Resolve-Path (Join-Path $gigsDir "..")).Path
$data    = if ($env:JOB_RADAR_DATA) { Join-Path $env:JOB_RADAR_DATA "gigs" } else { "C:\Users\endle\MyStuff\_job-radar-data\gigs" }
$state   = Join-Path $data "state"; $logs = Join-Path $data "logs"; $drafts = Join-Path $data "drafts"
foreach ($d in @($data, $state, $logs, $drafts)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
$ts  = Get-Date -Format "yyyy-MM-dd-HHmm"
$log = Join-Path $logs "run-$ts.log"
function Log([string]$m) { $l = "[$((Get-Date).ToString('HH:mm:ss'))] $m"; Write-Host $l; Add-Content -Path $log -Value $l -Encoding UTF8 }

$lock = Join-Path $state "RUNNING"
if (Test-Path $lock) {
    $old = Get-Content $lock -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue)) { Log "already running (pid $old); exit"; exit 0 }
}
Set-Content -Path $lock -Value $PID
$status = "failed"
try {
    Set-Location $repo
    Log "scout --days $Days"
    & python -X utf8 (Join-Path $gigsDir "scout.py") --days $Days --out $data 2>&1 | ForEach-Object { Log $_ }
    $newFile = Join-Path $data "new.jsonl"
    $n = if (Test-Path $newFile) { @(Get-Content $newFile | Where-Object { $_.Trim() }).Count } else { 0 }
    Log "new qualifying posts: $n"
    if ($n -gt 0) {
        $claude = "C:\Users\endle\AppData\Roaming\npm\claude.cmd"
        $promptFile = Join-Path $gigsDir "DRAFT-PROMPT.md"
        $outFile = Join-Path $drafts "$ts.md"
        $prompt = (Get-Content $promptFile -Raw) `
            -replace "\{\{NEW_JSONL\}\}", $newFile `
            -replace "\{\{PROOF_MD\}\}", (Join-Path $gigsDir "proof.md") `
            -replace "\{\{OUT_FILE\}\}", $outFile `
            -replace "\{\{MAX\}\}", $MaxDrafts
        Log "claude -p drafting -> $outFile"
        $prompt | & $claude -p --allowedTools "Read,Write" --max-turns 12 2>&1 | ForEach-Object { Log $_ }
        if (Test-Path $outFile) { Log "drafts written: $outFile" } else { Log "!! claude produced no draft file" }
    }
    $status = "ok"
} catch {
    Log "!! fatal: $($_.Exception.Message)"
    Add-Content -Path (Join-Path $state "crash.log") -Value "$ts $($_.Exception.Message)"
} finally {
    Set-Content -Path (Join-Path $state "LAST_RUN") -Value "$ts $status new=$n"
    Remove-Item $lock -ErrorAction SilentlyContinue
    Log "done ($status)"
}
