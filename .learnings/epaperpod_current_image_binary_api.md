# EpaperPod Current Image API Is Binary

`/api/current_image` returns PNG bytes, not JSON metadata. On Windows PowerShell, do not pipe it through `Select-Object -ExpandProperty Content` because it prints the entire byte stream.

For current-image verification, use `Invoke-WebRequest -OutFile ...` and compare `Get-FileHash` with the remote `current_image.png` hash, or inspect the saved PNG with `view_image`.
