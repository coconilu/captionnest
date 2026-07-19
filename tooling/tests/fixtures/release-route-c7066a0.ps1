$Version = $env:RELEASE_VERSION_INPUT.Trim()
if ($Version -ne $env:RELEASE_VERSION_INPUT) {
  throw 'The version must not contain leading or trailing whitespace.'
}
if ($Version -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$') {
  throw "Invalid version '$Version'. Use MAJOR.MINOR.PATCH without a v prefix."
}
$Prerelease = $env:RELEASE_PRERELEASE_INPUT.ToLowerInvariant()
if ($Prerelease -notin @('true', 'false')) {
  throw "Invalid prerelease value '$Prerelease'."
}

if ($env:GITHUB_REF -eq 'refs/heads/main') {
  $DispatchArgs = @(
    'workflow', 'run', 'prepare-release.yml',
    '--repo', $env:GITHUB_REPOSITORY,
    '--ref', 'main',
    '-f', "version=$Version",
    '-f', "prerelease=$Prerelease"
  )
  & gh @DispatchArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to dispatch Prepare Release for v$Version."
  }
  'mode=prepare' | Add-Content -LiteralPath $env:GITHUB_OUTPUT
  @"
  ## Prepare Release dispatched

  - Requested tag: ``v$Version``
  - Prerelease: ``$Prerelease``
  - This run only routed the request. Prepare Release will create or verify the annotated tag.
  - The tag-anchored Windows build and publish will appear as a separate Windows Release run.
  "@ | Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY
  exit 0
}

$ExpectedRef = "refs/tags/v$Version"
if ($env:GITHUB_REF -ne $ExpectedRef) {
  throw "Run Windows Release from main or dispatch it with --ref v$Version; got $env:GITHUB_REF."
}
'mode=build' | Add-Content -LiteralPath $env:GITHUB_OUTPUT
