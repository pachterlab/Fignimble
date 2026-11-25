# Install Fignimble on Windows: a Python environment with the engine and the editor (PySide6) in
# %LOCALAPPDATA%\Fignimble\python, the `fignimble` command on your PATH, and Fignimble in the Start menu.
#
#   powershell -ExecutionPolicy Bypass -File install_fignimble.ps1
#
# $env:PYTHON chooses the Python to build on (otherwise the `py` launcher's Python 3, or python on the PATH).
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Support = Join-Path $env:LOCALAPPDATA "Fignimble"
$Env_ = Join-Path $Support "python"
$Bin = Join-Path $Support "bin"

function Find-Python {
    if ($env:PYTHON) { return @($env:PYTHON) }
    if (Get-Command py -ErrorAction SilentlyContinue) { return @("py", "-3") }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @("python") }
    throw "Fignimble needs Python 3.10 or later: install it from python.org (or: winget install Python.Python.3.12)"
}
$Py = Find-Python
$PyExe = $Py[0]; $PyArgs = @($Py | Select-Object -Skip 1)
& $PyExe @PyArgs -c "import sys; assert sys.version_info >= (3, 10), 'Fignimble needs Python 3.10 or later'"
if ($LASTEXITCODE -ne 0) { throw "Fignimble needs Python 3.10 or later" }

$VPy = Join-Path $Env_ "Scripts\python.exe"
if (-not (Test-Path $VPy)) {
    Write-Host "making a Python environment in $Env_"
    New-Item -ItemType Directory -Force -Path $Support | Out-Null
    & $PyExe @PyArgs -m venv $Env_
    if ($LASTEXITCODE -ne 0) { throw "couldn't make the environment" }
}
function Pip { & $VPy -m pip install -q @args; if ($LASTEXITCODE -ne 0) { throw "pip install $args failed" } }
Pip --upgrade pip
Pip -r (Join-Path $Root "engine\requirements.txt")
Pip "PySide6-Essentials>=6.5"
Pip -e $Root                                        # `import fignimble` there, and the editor (apps/desktop)

# the fignimble command, in a folder of its own on the user's PATH
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$Cmd = Join-Path $Root "bin\fignimble.cmd"
Set-Content -Path (Join-Path $Bin "fignimble.cmd") -Encoding ASCII -Value "@echo off`r`ncall `"$Cmd`" %*`r`n"
Set-Content -Path (Join-Path $Bin "fignimble-app.cmd") -Encoding ASCII `
    -Value "@echo off`r`nstart `"`" `"$(Join-Path $Env_ 'Scripts\pythonw.exe')`" -m fignimble_app %*`r`n"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $UserPath) { $UserPath = "" }
if (($UserPath -split ";") -notcontains $Bin) {
    [Environment]::SetEnvironmentVariable("Path", ($UserPath.TrimEnd(";") + ";" + $Bin).TrimStart(";"), "User")
    Write-Host "added $Bin to your PATH (new terminals have it)"
}

# an icon from the SVG (PyMuPDF draws it, Pillow writes the .ico), and the Start menu shortcut
$Ico = Join-Path $Support "fignimble.ico"
$Svg = Join-Path $Root "apps\desktop\fignimble_app\assets\fignimble.svg"
& $VPy -c "import sys, io, pymupdf; from PIL import Image; d = pymupdf.open(sys.argv[1]); p = d[0].get_pixmap(dpi=288, alpha=True); Image.open(io.BytesIO(p.tobytes('png'))).save(sys.argv[2], sizes=[(16,16),(32,32),(48,48),(256,256)])" $Svg $Ico
$Shell = New-Object -ComObject WScript.Shell
$Programs = [Environment]::GetFolderPath("Programs")
$Lnk = $Shell.CreateShortcut((Join-Path $Programs "Fignimble.lnk"))
$Lnk.TargetPath = Join-Path $Env_ "Scripts\pythonw.exe"
$Lnk.Arguments = "-m fignimble_app"
$Lnk.WorkingDirectory = $env:USERPROFILE
$Lnk.Description = "Multi-panel figures for publication, assembled from Python"
if (Test-Path $Ico) { $Lnk.IconLocation = $Ico }
$Lnk.Save()

Write-Host "installed: Fignimble in the Start menu, and the fignimble and fignimble-app commands ($Bin)"
if (-not ((Get-Command gswin64c -ErrorAction SilentlyContinue) -or (Get-Command gswin32c -ErrorAction SilentlyContinue) -or
          (Test-Path (Join-Path $env:ProgramFiles "gs")))) {
    Write-Host "note: EPS, CMYK and outlined-text export need Ghostscript (ghostscript.com, or: winget install ArtifexSoftware.GhostScript)"
}
Write-Host "try: fignimble-app `"$(Join-Path $Root 'examples\demo')`"   (in a new terminal)"
