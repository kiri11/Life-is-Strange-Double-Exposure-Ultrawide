# Copies the source of every binary in a release archive into it, so the
# archive is its own corresponding source under GPL-3 section 6. Run from
# the repository root: ./scripts/stage-source.ps1 <staging folder>
param([Parameter(Mandatory = $true)][string]$Root)
$ErrorActionPreference = 'Stop'
foreach ($f in 'Cargo.toml', 'Cargo.lock', '.cargo/config.toml') {
    New-Item -ItemType Directory -Path (Split-Path "$Root/$f") -Force | Out-Null
    Copy-Item $f "$Root/$f"
}
New-Item -ItemType Directory -Path "$Root/crates" -Force | Out-Null
foreach ($crate in Get-ChildItem -Directory crates) {
    $dest = "$Root/crates/$($crate.Name)"
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    foreach ($f in Get-ChildItem $crate.FullName -File) { Copy-Item $f.FullName "$dest/$($f.Name)" }
    foreach ($d in 'src', 'tests') {
        if (Test-Path "$($crate.FullName)/$d") { Copy-Item -Recurse "$($crate.FullName)/$d" "$dest/$d" }
    }
}
New-Item -ItemType Directory -Path "$Root/tests" -Force | Out-Null
Copy-Item -Recurse tests/kraken "$Root/tests/kraken"
