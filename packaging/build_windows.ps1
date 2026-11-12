#Requires -Version 5.1
<#
.SYNOPSIS
    Build the Windows parts of a DeepStrata release.

.DESCRIPTION
    Runs on a Windows x64 PC (or a Windows CI runner) and writes a folder that
    `python packaging/release.py build --windows-parts <folder>` imports on the
    release PC. It builds, but never signs, and it never sees the feed key:

      * the Terminal/Control archive  (strata-terminal-<version>-windows-x86_64.zip,
        with the pinned CPython, Node and the page inside);
      * the Qwen CUDA 13 engine package (strata.exe, strata_kq.dll,
        strata-vision.exe);
      * the DeepSeek V4.1-Flash engine package (strata-dsv41.exe, feed member
        `dsv41`).

    The result is a self-contained folder:

        windows-parts.json                 commit, versions, toolchain, hashes
        terminal/*.zip                     the Terminal archive
        engines/<name>/                    the Qwen engine package (unsigned manifest)
        dsv41/<name>/                      the DeepSeek engine package (unsigned manifest)
        smoke/smoke-terminal.json          the Terminal smoke report (run on this PC)

    The release PC signs both engine manifests with the feed key and packs the
    archives, so a build machine needs no secret. Nothing here is uploaded.

    CUDA 13 is required (the release is the cuda13 variant). Visual Studio 2022
    with the C++ workload, CMake, Ninja, Node 24.12+, Python 3.10+ and git must
    be installed. Run from anywhere; the script finds the repository beside it.

.PARAMETER OutDir
    Where the parts folder is written. Default: <repo>\build-windows-parts.
    A previous folder is replaced, so run with -Clean after changing the tree.

.PARAMETER Commit
    The commit the parts must come from. Default: the checkout's HEAD. The
    release PC refuses parts built from another commit.

.PARAMETER Version
    The component version (default: CMakeLists.txt's project VERSION, the same
    number Control and the engines carry).

.PARAMETER CudaRoot
    The CUDA 13 toolkit root. Default: CUDA_PATH, then the usual install folder.

.PARAMETER BuildDir
    Where CMake builds the engine. Default: <repo>\build-windows-cuda13.

.PARAMETER GgmlDir
    The pinned llama.cpp checkout. Default: <repo>\third_party\llama.cpp.

.PARAMETER Only
    Build only these parts: terminal, qwen, dsv41. Default: all three.

.PARAMETER SkipHost
    Reuse an existing apps\host\dist instead of running npm ci / npm run build.

.PARAMETER SkipSmoke
    Do not run the Terminal smoke test (the release tool then refuses the parts;
    only for a quick iteration on a build).

.PARAMETER AllowDirty
    Build from a tree with uncommitted changes. The manifest records dirty=true
    and the release tool refuses it: this is for local iteration only.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1

.EXAMPLE
    pwsh -File packaging\build_windows.ps1 -OutDir D:\strata-parts -Only terminal,qwen
#>
[CmdletBinding()]
param(
    [string]$OutDir = "",
    [string]$Commit = "",
    [string]$Version = "",
    [string]$CudaRoot = "",
    [string]$BuildDir = "",
    [string]$GgmlDir = "",
    [string[]]$Only = @("terminal", "qwen", "dsv41"),
    [switch]$SkipHost,
    [switch]$SkipSmoke,
    [switch]$AllowDirty
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Repo "CMakeLists.txt"))) {
    throw "this script must sit in <repo>\packaging (found repo '$Repo')"
}
if (-not $OutDir) { $OutDir = Join-Path $Repo "build-windows-parts" }
$OutDir = [IO.Path]::GetFullPath($OutDir)
if (-not $BuildDir) { $BuildDir = Join-Path $Repo "build-windows-cuda13" }
if (-not $GgmlDir) { $GgmlDir = Join-Path $Repo "third_party\llama.cpp" }

$Wanted = @()
foreach ($item in $Only) {
    foreach ($part in ($item -split ",")) {
        $name = $part.Trim().ToLowerInvariant()
        if ($name -and ($Wanted -notcontains $name)) { $Wanted += $name }
    }
}
foreach ($name in $Wanted) {
    if (@("terminal", "qwen", "dsv41") -notcontains $name) {
        throw "-Only takes terminal, qwen and/or dsv41 (got '$name')"
    }
}

# ---------------------------------------------------------------------------------------------- small helpers

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "== $Message" -ForegroundColor Cyan
}

# Windows PowerShell 5.1 reports a native program's stderr as an error record, and this script runs with
# $ErrorActionPreference = "Stop": one warning that npm, cmake or ninja wrote to stderr would end the build with a
# "NativeCommandError" that says nothing about what went wrong. Everything below runs with "Continue" and decides by the
# exit code the program returned, which is what tells a warning from a failure.
function Invoke-Quietly([scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        return & $Command
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkDir = $Repo
    )
    Write-Host ">> $FilePath $($Arguments -join ' ')"
    Push-Location $WorkDir
    try {
        Invoke-Quietly { & $FilePath @Arguments 2>&1 | ForEach-Object { Write-Host $_ } } | Out-Null
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($code -ne 0) {
        throw "$FilePath exited with code $code"
    }
}

function Invoke-CommandLine {
    # a batch file prints its progress on stderr, the same warning-versus-failure rule as above
    param([Parameter(Mandatory)][string]$Line)
    Write-Host ">> cmd /c $Line"
    Invoke-Quietly { & cmd.exe /c $Line 2>&1 | ForEach-Object { Write-Host $_ } } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "cmd /c $Line exited with code $LASTEXITCODE"
    }
}

function Get-ToolOutput {
    param([string]$FilePath, [string[]]$Arguments = @())
    try {
        $lines = Invoke-Quietly { & $FilePath @Arguments 2>&1 }
        return (($lines | Out-String).Trim())
    }
    catch {
        return ""
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Get-RelPosix([string]$Path, [string]$Base) {
    $full = [IO.Path]::GetFullPath($Path)
    $baseFull = [IO.Path]::GetFullPath($Base).TrimEnd('\', '/')
    if (-not $full.StartsWith($baseFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$full is outside $baseFull"
    }
    return $full.Substring($baseFull.Length).TrimStart('\', '/').Replace('\', '/')
}

function Get-FirstMatch([string]$Text, [string]$Pattern, [string]$What) {
    $match = [regex]::Match($Text, $Pattern, [Text.RegularExpressions.RegexOptions]::Singleline)
    if (-not $match.Success) { throw "could not read $What" }
    return $match.Groups[1].Value
}

function Find-VcVars {
    $candidates = @()
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $found = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
        if ($found) {
            $candidates += (Join-Path ($found | Select-Object -First 1).Trim() "VC\Auxiliary\Build\vcvars64.bat")
        }
    }
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $root) { continue }
        foreach ($edition in @("Enterprise", "Professional", "Community", "BuildTools")) {
            $candidates += (Join-Path $root "Microsoft Visual Studio\2022\$edition\VC\Auxiliary\Build\vcvars64.bat")
        }
    }
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) { return $path }
    }
    return $null
}

function Import-VcVars([string]$VcVars) {
    Write-Host ">> using $VcVars"
    $lines = Invoke-Quietly { & cmd.exe /c "call `"$VcVars`" >nul 2>&1 && set" }
    if ($LASTEXITCODE -ne 0) { throw "could not set up the Visual Studio environment from $VcVars" }
    foreach ($line in $lines) {
        $index = $line.IndexOf("=")
        if ($index -gt 0) {
            $name = $line.Substring(0, $index)
            $value = $line.Substring($index + 1)
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

function Find-CudaRoot([string]$Explicit) {
    $candidates = @()
    if ($Explicit) { $candidates += $Explicit }
    if ($env:CUDA_PATH) { $candidates += $env:CUDA_PATH }
    if ($env:CUDA_PATH_V13_0) { $candidates += $env:CUDA_PATH_V13_0 }
    $candidates += (Join-Path $env:ProgramFiles "NVIDIA GPU Computing Toolkit\CUDA\v13.0")
    foreach ($path in $candidates) {
        if ($path -and (Test-Path (Join-Path $path "bin\nvcc.exe"))) { return [IO.Path]::GetFullPath($path) }
    }
    throw "CUDA 13 was not found. Install it, set CUDA_PATH, or pass -CudaRoot."
}

function Find-Python {
    foreach ($name in @("python", "py")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) {
            if ($name -eq "py") { return @{ File = $command.Source; Arguments = @("-3") } }
            return @{ File = $command.Source; Arguments = @() }
        }
    }
    throw "Python 3.10+ is required (python or py was not found)"
}

# ---------------------------------------------------------------------------------------------- the tree and its versions

Set-Location $Repo

Write-Step "checking the checkout"
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) { throw "git is required" }
$head = (& git rev-parse HEAD).Trim()
if (-not $head) { throw "could not read the commit (git rev-parse HEAD)" }
if ($Commit) {
    if ($Commit -ne $head -and -not $head.StartsWith($Commit)) {
        throw "the checkout is at $head, not at the requested commit $Commit"
    }
    $head = (& git rev-parse $Commit).Trim()
}
$dirty = -not [string]::IsNullOrWhiteSpace((& git status --porcelain --untracked-files=no | Out-String))
if ($dirty -and -not $AllowDirty) {
    throw "the tree has uncommitted changes. Commit them, or pass -AllowDirty for a local test build (the release tool refuses such parts)."
}
if ($dirty) { Write-Warning "building from a dirty tree: the release tool will refuse these parts" }

$component = $Version
if (-not $component) {
    $component = Get-FirstMatch -Text (Get-Content (Join-Path $Repo "CMakeLists.txt") -Raw) `
        -Pattern 'project\s*\([^)]*?VERSION\s+([0-9]+(?:\.[0-9]+)+)' -What "the component version in CMakeLists.txt"
}
$product = Get-FirstMatch -Text (Get-Content (Join-Path $Repo "strata_cli\__init__.py") -Raw) `
    -Pattern 'PRODUCT_VERSION\s*=\s*"([0-9]+(?:\.[0-9]+)+)"' -What "PRODUCT_VERSION"
$hostVersion = (Get-Content (Join-Path $Repo "apps\host\package.json") -Raw | ConvertFrom-Json).version
if (-not $hostVersion) { throw "apps\host\package.json has no version" }

$python = Find-Python
$pythonVersion = Get-ToolOutput $python.File ($python.Arguments + @("--version"))
$node = Get-Command node -ErrorAction SilentlyContinue
$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($Wanted -contains "terminal" -and -not $SkipHost) {
    if (-not $node -or -not $npm) { throw "Node 24.12+ and npm are required to build the page (or pass -SkipHost with apps\host\dist already built)" }
    $nodeVersion = Get-ToolOutput $node.Source @("--version")
    $nodeNumbers = [regex]::Match($nodeVersion, 'v(\d+)\.(\d+)\.(\d+)')
    if (-not $nodeNumbers.Success -or [int]$nodeNumbers.Groups[1].Value -lt 24) {
        throw "Node $nodeVersion is too old: the App Host needs 24.12 or newer"
    }
    if ([int]$nodeNumbers.Groups[1].Value -eq 24 -and [int]$nodeNumbers.Groups[2].Value -lt 12) {
        throw "Node $nodeVersion is too old: the App Host needs 24.12 or newer"
    }
}

$cudaRoot = $null
$cudaVersion = $null
if (($Wanted -contains "qwen") -or ($Wanted -contains "dsv41")) {
    $cudaRoot = Find-CudaRoot $CudaRoot
    $nvccText = Get-ToolOutput (Join-Path $cudaRoot "bin\nvcc.exe") @("--version")
    $cudaVersion = Get-FirstMatch -Text $nvccText -Pattern 'release\s+(\d+\.\d+)' -What "the CUDA version of nvcc"
    if (-not $cudaVersion.StartsWith("13.")) {
        throw "CUDA $cudaVersion was found in $cudaRoot, but this release builds the cuda13 variant (CUDA 13.x)"
    }
    if (-not (Test-Path (Join-Path $GgmlDir "CMakeLists.txt"))) {
        throw "the pinned llama.cpp was not found in $GgmlDir (python -c `"import setup; setup.get_llama_cpp()`")"
    }
}

Write-Host "  commit     $head"
Write-Host "  component  $component (page $hostVersion, product $product)"
Write-Host "  python     $pythonVersion"
if ($cudaRoot) { Write-Host "  cuda       $cudaVersion ($cudaRoot)" }
Write-Host "  parts      $OutDir"

# The parts folder is replaced: never mix two builds.
if (Test-Path $OutDir) {
    Write-Host "removing the previous parts folder $OutDir"
    Remove-Item -Recurse -Force -LiteralPath $OutDir
}
$terminalDir = Join-Path $OutDir "terminal"
$engineDir = Join-Path $OutDir "engines"
$dsv41Dir = Join-Path $OutDir "dsv41"
$smokeDir = Join-Path $OutDir "smoke"
$stageRoot = Join-Path $OutDir "stage"
foreach ($path in @($terminalDir, $engineDir, $dsv41Dir, $smokeDir, $stageRoot)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

$toolchain = [ordered]@{
    cuda   = $cudaVersion
    python = $pythonVersion
    node   = $(if ($node) { Get-ToolOutput $node.Source @("--version") } else { "" })
    cmake  = $(if (Get-Command cmake -ErrorAction SilentlyContinue) { Get-ToolOutput "cmake" @("--version") } else { "" })
    ninja  = $(if (Get-Command ninja -ErrorAction SilentlyContinue) { Get-ToolOutput "ninja" @("--version") } else { "" })
    git    = (Get-ToolOutput "git" @("--version"))
}
$manifest = [ordered]@{
    schema_version    = 1
    platform          = "windows-x86_64"
    commit            = $head
    dirty             = $dirty
    component_version = $component
    product_version   = $product
    host_version      = $hostVersion
    built_at          = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    source            = "packaging/build_windows.ps1"
    toolchain         = $toolchain
    components        = [ordered]@{}
    smoke             = [ordered]@{}
}

# ---------------------------------------------------------------------------------------------- the page

if ($Wanted -contains "terminal" -and -not $SkipHost) {
    Write-Step "building the App Host (the browser page)"
    $hostDir = Join-Path $Repo "apps\host"
    if (-not (Test-Path (Join-Path $hostDir "node_modules"))) {
        Invoke-Native -FilePath $npm.Source -Arguments @("ci", "--no-audit", "--no-fund") -WorkDir $hostDir
    }
    Invoke-Native -FilePath $npm.Source -Arguments @("run", "build") -WorkDir $hostDir
    if (-not (Test-Path (Join-Path $hostDir "dist\index.html"))) {
        throw "apps\host\dist\index.html is still missing after npm run build"
    }
}

# ---------------------------------------------------------------------------------------------- the Terminal/Control archive

if ($Wanted -contains "terminal") {
    Write-Step "building the Terminal archive (pinned Python, Node and page inside)"
    $terminalArgs = @()
    $terminalArgs += $python.Arguments
    $terminalArgs += @("packaging\package_terminal.py", "--out", $terminalDir, "--target", "windows-x86_64",
        "--version", $component, "--commit", $head)
    Invoke-Native -FilePath $python.File -Arguments $terminalArgs
    $zip = Join-Path $terminalDir "strata-terminal-$component-windows-x86_64.zip"
    if (-not (Test-Path $zip)) {
        $found = @(Get-ChildItem -Path $terminalDir -Filter "*.zip" -File)
        if ($found.Count -ne 1) { throw "package_terminal.py finished but $zip is missing" }
        $zip = $found[0].FullName
    }
    $manifest.components["terminal"] = [ordered]@{
        archive = (Get-RelPosix $zip $OutDir)
        sha256  = (Get-Sha256 $zip)
        bytes   = (Get-Item $zip).Length
        format  = "zip"
    }

    if (-not $SkipSmoke) {
        Write-Step "smoke-testing the Terminal archive (bundled Python, Node, page, mock server)"
        $smokeJson = Join-Path $smokeDir "smoke-terminal.json"
        $smokeArgs = $python.Arguments + @("packaging\smoke_terminal.py", $zip, "--json", $smokeJson)
        Invoke-Native -FilePath $python.File -Arguments $smokeArgs
        if (-not (Test-Path $smokeJson)) { throw "smoke_terminal.py wrote no report to $smokeJson" }
        $report = Get-Content $smokeJson -Raw | ConvertFrom-Json
        if (-not $report.ok) { throw "the Terminal smoke test failed; see $smokeJson" }
        $manifest.smoke["terminal"] = [ordered]@{
            report = (Get-RelPosix $smokeJson $OutDir)
            sha256 = (Get-Sha256 $smokeJson)
        }
    }
    else {
        Write-Warning "Terminal smoke test skipped: the release tool will refuse these parts (--skip-smoke/--SkipSmoke)"
    }
}

# ---------------------------------------------------------------------------------------------- the engine packages

$vcvars = $null
if (($Wanted -contains "qwen") -or ($Wanted -contains "dsv41")) {
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        $vcvars = Find-VcVars
        if (-not $vcvars) {
            throw "Visual Studio 2022 with the C++ workload was not found (vcvars64.bat)"
        }
        Import-VcVars $vcvars
    }
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        throw "cl.exe is still not on PATH after setting up Visual Studio"
    }
}

if ($Wanted -contains "qwen") {
    Write-Step "building the Qwen CUDA 13 engine (sm_75, sm_86, sm_89, sm_120 + PTX 120)"
    $build = [IO.Path]::GetFullPath($BuildDir)
    $cmakeArgs = @(
        "-G", "Ninja", "-S", $Repo, "-B", $build,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DSTRATA_ENABLE_CUDA=ON",
        "-DSTRATA_BUILD_TESTS=OFF",
        "-DSTRATA_EXPERIMENTAL_SM60=OFF",
        "-DSTRATA_NATIVE_EXPERTS=ON",
        "-DSTRATA_PORTABLE=ON",
        "-DSTRATA_Q6K_EXPERTS=ON",
        "-DSTRATA_MMQ_KQUANTS=ON",
        "-DCMAKE_CUDA_ARCHITECTURES=75-real;86-real;89-real;120-real;120-virtual",
        "-DCMAKE_CUDA_COMPILER=$(Join-Path $cudaRoot 'bin\nvcc.exe')",
        "-DCUDAToolkit_ROOT=$cudaRoot",
        "-DSTRATA_GGML_DIR=$GgmlDir"
    )
    Invoke-Native -FilePath "cmake" -Arguments $cmakeArgs
    $jobs = [Math]::Max(2, [Environment]::ProcessorCount)
    Invoke-Native -FilePath "cmake" -Arguments @("--build", $build, "--target", "strata", "strata_kq", "-j", "$jobs")

    $stage = Join-Path $stageRoot "qwen"
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    foreach ($file in @("strata.exe", "strata_kq.dll")) {
        $source = Join-Path $build $file
        if (-not (Test-Path $source)) { throw "the engine build produced no $file in $build" }
        Copy-Item -LiteralPath $source -Destination $stage -Force
    }
    $license = Join-Path $Repo "LICENSE"
    if (Test-Path $license) { Copy-Item -LiteralPath $license -Destination $stage -Force }
    $buildJson = [ordered]@{
        version      = $component
        source       = "packaging/build_windows.ps1"
        archs        = "75;86;89;120"
        cuda         = $cudaVersion
        portable     = $true
        commit       = $head
    }
    $buildJson | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 -Path (Join-Path $stage "BUILD.json")

    Write-Step "building the CUDA picture reader for the same architectures"
    $vision = Join-Path $Repo "packaging\build_vision_windows.bat"
    Invoke-CommandLine "`"$vision`" cuda13 `"$stage`""
    if (-not (Test-Path (Join-Path $stage "strata-vision.exe"))) { throw "the picture reader did not land in $stage" }

    Write-Step "packaging the Qwen engine"
    $packageArgs = $python.Arguments + @(
        "packaging\package_engine.py", "--src", $stage, "--backend", "cuda", "--version", $component,
        "--out", $engineDir, "--platform", "windows-x86_64", "--variant", "cuda13",
        "--arch", "sm_75", "--arch", "sm_86", "--arch", "sm_89", "--arch", "sm_120", "--arch", "compute_120",
        "--min-driver", "nvidia=580.65", "--build-id", $head.Substring(0, 12)
    )
    Invoke-Native -FilePath $python.File -Arguments $packageArgs
    $engineFolder = Join-Path $engineDir ("{0}-windows-x86_64-cuda-cuda13" -f $component)
    if (-not (Test-Path $engineFolder)) {
        $found = @(Get-ChildItem -Path $engineDir -Directory | Where-Object { $_.Name -like "*-windows-x86_64-cuda-cuda13" })
        if ($found.Count -ne 1) { throw "the Qwen package was not written where expected ($engineDir)" }
        $engineFolder = $found[0].FullName
    }
    Invoke-Native -FilePath $python.File -Arguments ($python.Arguments + @("packaging\package_engine.py", "--verify", $engineFolder))
    $manifest.components["qwen"] = [ordered]@{
        folder          = (Get-RelPosix $engineFolder $OutDir)
        manifest_sha256 = (Get-Sha256 (Join-Path $engineFolder "engine-component.json"))
        files           = @(Get-ChildItem -Recurse -File -LiteralPath $engineFolder).Count
        bytes           = (Get-ChildItem -Recurse -File -LiteralPath $engineFolder | Measure-Object Length -Sum).Sum
    }
}

if ($Wanted -contains "dsv41") {
    Write-Step "building the DeepSeek V4.1-Flash engine (strata-dsv41)"
    $build = Join-Path $OutDir "build-dsv41"
    New-Item -ItemType Directory -Force -Path $build | Out-Null
    $env:STRATA_GGML_DIR = $GgmlDir
    $env:CUDA_ROOT = $cudaRoot
    if (-not $env:BUILD_JOBS) { $env:BUILD_JOBS = "$([Math]::Max(2, [Environment]::ProcessorCount))" }
    if (-not $env:DSV41_ARCH) { $env:DSV41_ARCH = "86-real;89-real;120" }
    $dsv41Bat = Join-Path $Repo "packaging\build_dsv41_windows.bat"
    Invoke-CommandLine "`"$dsv41Bat`" `"$build`" `"$Repo`""
    $exe = Join-Path $build "strata-dsv41.exe"
    if (-not (Test-Path $exe)) { throw "build_dsv41_windows.bat finished but $exe is missing" }

    $stage = Join-Path $stageRoot "dsv41"
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    Copy-Item -LiteralPath $exe -Destination $stage -Force
    $license = Join-Path $Repo "LICENSE"
    if (Test-Path $license) { Copy-Item -LiteralPath $license -Destination $stage -Force }
    [ordered]@{
        version  = $component
        source   = "packaging/build_dsv41_windows.bat"
        archs    = "86;89;120"
        cuda     = $cudaVersion
        portable = $true
        commit   = $head
    } | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 -Path (Join-Path $stage "BUILD.json")

    Write-Step "packaging the DeepSeek engine"
    $packageArgs = $python.Arguments + @(
        "packaging\package_engine.py", "--src", $stage, "--backend", "cuda", "--version", $component,
        "--out", $dsv41Dir, "--platform", "windows-x86_64", "--variant", "cuda13",
        "--entrypoint", "strata-dsv41.exe",
        "--arch", "sm_86", "--arch", "sm_89", "--arch", "sm_120", "--arch", "compute_120",
        "--min-driver", "nvidia=580.65", "--build-id", $head.Substring(0, 12), "--require-dsv41"
    )
    Invoke-Native -FilePath $python.File -Arguments $packageArgs
    $dsv41Folder = Join-Path $dsv41Dir ("{0}-windows-x86_64-cuda-cuda13" -f $component)
    if (-not (Test-Path $dsv41Folder)) {
        $found = @(Get-ChildItem -Path $dsv41Dir -Directory | Where-Object { $_.Name -like "*-windows-x86_64-cuda-cuda13" })
        if ($found.Count -ne 1) { throw "the DeepSeek package was not written where expected ($dsv41Dir)" }
        $dsv41Folder = $found[0].FullName
    }
    Invoke-Native -FilePath $python.File -Arguments ($python.Arguments + @("packaging\package_engine.py", "--verify", $dsv41Folder, "--require-dsv41"))
    $manifest.components["dsv41"] = [ordered]@{
        folder          = (Get-RelPosix $dsv41Folder $OutDir)
        manifest_sha256 = (Get-Sha256 (Join-Path $dsv41Folder "engine-component.json"))
        files           = @(Get-ChildItem -Recurse -File -LiteralPath $dsv41Folder).Count
        bytes           = (Get-ChildItem -Recurse -File -LiteralPath $dsv41Folder | Measure-Object Length -Sum).Sum
    }
}

# ---------------------------------------------------------------------------------------------- the manifest

Write-Step "writing windows-parts.json"
$manifestPath = Join-Path $OutDir "windows-parts.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -Path $manifestPath
if (-not (Test-Path $manifestPath)) { throw "could not write $manifestPath" }

Write-Host ""
Write-Host "parts: $OutDir"
foreach ($name in @("terminal", "qwen", "dsv41")) {
    if ($manifest.components.Contains($name)) {
        $entry = $manifest.components[$name]
        if ($entry.Contains("folder")) { Write-Host ("  {0,-9} {1}" -f $name, $entry.folder) }
        else { Write-Host ("  {0,-9} {1} ({2:N1} MiB)" -f $name, $entry.archive, ($entry.bytes / 1MB)) }
    }
}
Write-Host ""
Write-Host "Next, on the release PC (the one with the feed key):"
Write-Host "  python packaging/release.py build --windows-parts `"$OutDir`""
Write-Host ""
