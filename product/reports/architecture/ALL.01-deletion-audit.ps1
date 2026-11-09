# ALL.01 / #159 deletion-target audit (repository-wide, at the final integration tip)
# Run from the worktree root:  & .\product\reports\architecture\ALL.01-deletion-audit.ps1
#
# Categories:
#   production = product/internal product/cmd product/apps product/tools product/deploy
#                product/contracts product/migrations tools deploy contracts examples
#   tests      = product/tests
#   historical = product/reports product/PROGRESS.md product/docs docs research and the
#                root specification markdown (report/ADR/spec/handoff prose, not code)
# git grep exit 1 = no match (expected for deleted symbols).
$ErrorActionPreference = 'Continue'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..\..')
Set-Location $repo

$prodPath = @('product/internal','product/cmd','product/apps','product/tools','product/deploy','product/contracts','product/migrations','tools','deploy','contracts','examples')
$testPath = @('product/tests')
$histPath = @('product/reports','product/PROGRESS.md','product/docs','docs','research','*.md')

$groups = [ordered]@{
  'C2 model attempt lifecycle' = @('dispatchStream','dispatchStreamFallback','dispatchModelFallback','dispatchModel','dispatchFallback')
  'C3 verified chunk read'     = @('readChunkText','validateCachedItems','cacheHitReadFailureReason','cacheDiscardDecision','matchesSealedBody','cacheBodySHA256')
  'C4 fake linear view'        = @('graphLinearView','linearRun\.available','linearRun\.skip','graphNodeView\.start','graphNodeAttempt\.started')
  'C5 ticket-number supports'  = @('T024Stack','T029Stack','T031Stack','T033Stack','run_linear_once')
  'C6 page run-state fallback' = @('runStream[A-Za-z]*','RunStateDependencyUnavailable')
}

"# ALL.01 deletion audit - commit $(git rev-parse --short HEAD) - $(Get-Date -Format o)"
"# search space: git grep over the whole worktree (tracked files); categories below."
"#   production: product/{internal,cmd,apps,tools,deploy,contracts,migrations} tools deploy contracts examples"
"#   tests:      product/tests"
"#   historical: product/reports product/PROGRESS.md product/docs docs research root *.md"
""
foreach ($g in $groups.Keys) {
  "## $g"
  foreach ($p in $groups[$g]) {
    $all = @(git grep -n -E $p -- . 2>$null)
    $prod = @(git grep -n -E $p -- @prodPath 2>$null)
    $test = @(git grep -n -E $p -- @testPath 2>$null)
    $hist = @(git grep -n -E $p -- @histPath 2>$null)
    "pattern {0,-34} all={1,-3} production={2,-3} tests={3,-3} historical={4}" -f $p, $all.Count, $prod.Count, $test.Count, $hist.Count
    if ($prod.Count -gt 0) { $prod | ForEach-Object { "  PROD  $_" } }
    if ($test.Count -gt 0) { $test | ForEach-Object { "  TEST  $_" } }
  }
  ""
}
"## C2 old lifecycle loops (production, internal/capabilities only)"
"expected: exactly one long-lived attempt loop, in internal/capabilities/attempt.go"
@(git grep -n -E 'for attempt := 1; attempt <= maxPhysicalAttempts' -- product/internal/capabilities 2>$null) | ForEach-Object { "  LOOP  $_" }
"dispatchModel hits above are the content handler of the same private session (service.go:1038); dispatchFallback is session.dispatchFallback in the unified driver (attempt.go:428)."
""
"## C5 AST support-contract check (product/reports/architecture/C5.04-contract-check.py --phase all)"
Set-Location (Join-Path $repo 'product')
& D:\Python\python.exe -X utf8 reports/architecture/C5.04-contract-check.py --phase all *>&1 | ForEach-Object { "  $_" }
"exit=$LASTEXITCODE"
Set-Location $repo
"exit=$LASTEXITCODE"
