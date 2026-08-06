$root = $env:PKG_ROOT
$out  = $env:PKG_OUT
$dirs = @('src', 'web', 'docs')

Add-Type -Assembly 'System.IO.Compression.FileSystem'

$zip = [IO.Compression.ZipFile]::Open($out, 'Create')

$reparse = Get-ChildItem -Path $root -Recurse -Force -Directory |
    Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
    ForEach-Object { $_.FullName + '\' }

foreach ($d in $dirs) {
    $base = Join-Path $root $d
    if (-not (Test-Path $base)) {
        Write-Host "  Skipping $d/ (not found)"
        continue
    }
    $files = Get-ChildItem -Path $base -Recurse -File -Force |
        Where-Object {
            $f = $_.FullName
            -not ($reparse | Where-Object { $f.StartsWith($_) })
        }
    foreach ($f in $files) {
        $entry = $f.FullName.Substring($root.Length + 1).Replace('\', '/')
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $entry) | Out-Null
    }
    Write-Host "  Added $d/ ($($files.Count) files)"
}

$zip.Dispose()
Write-Host ''
Write-Host "  Done -> $out"
