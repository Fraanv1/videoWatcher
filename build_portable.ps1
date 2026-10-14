# Builds dist\videoWatcher\ (and dist\videoWatcher_portable.zip): a folder that runs on a
# Windows 10/11 x64 PC with nothing installed. Needs Python 3.11+ on THIS (build) machine only.
#
#   powershell -ExecutionPolicy Bypass -File build_portable.ps1 [-FfmpegPath <ffmpeg.exe>]

param(
    [string]$FfmpegPath = "$PSScriptRoot\..\General_Beta_VMS Pro_Win64\General_Beta_VMS Pro_Win64\ffmpeg.exe"
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = "$root\.build\venv"
$dist = "$root\dist"
$out = "$dist\videoWatcher"

if (-not (Test-Path "$venv\Scripts\python.exe")) {
    python -m venv $venv
}
& "$venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$venv\Scripts\python.exe" -m pip install --quiet "pyinstaller>=6.10" "fastapi>=0.110" "uvicorn>=0.29"
if ($LASTEXITCODE) { throw "pip install failed" }

if (Test-Path $out) { Remove-Item -Recurse -Force $out }
& "$venv\Scripts\pyinstaller.exe" --noconfirm --clean --onedir --console `
    --name videoWatcher `
    --distpath $dist --workpath "$root\.build\work" --specpath "$root\.build" `
    --add-data "$root\videowatcher\static;videowatcher\static" `
    --collect-submodules uvicorn `
    "$root\launcher.py"
if ($LASTEXITCODE) { throw "pyinstaller failed" }

if (Test-Path $FfmpegPath) {
    Copy-Item $FfmpegPath "$out\ffmpeg.exe"
    Set-Content -Encoding utf8 "$out\FFMPEG_LICENCIA.txt" @"
ffmpeg.exe es FFmpeg (https://ffmpeg.org), software libre bajo licencia GPL.
Esta copia proviene de la instalación de VMS Pro y se usa sin modificar.
El código fuente de FFmpeg está disponible en https://ffmpeg.org/download.html
"@
} else {
    Write-Warning "ffmpeg.exe not found at $FfmpegPath; the package will save .h264 only (no MP4)."
}

Copy-Item "$root\LEEME.md", "$root\README.md", "$root\EVIDENCE.md" $out
Set-Content -Encoding utf8 "$out\LEEME_PRIMERO.txt" @"
videoWatcher (versión portable, no hace falta instalar nada)
============================================================

1. Copiá esta carpeta completa a la PC (por ejemplo al Escritorio).
   No la pongas en "Archivos de programa".
2. Doble clic en videoWatcher.exe y elegí una opción:
     1) Abrir videoWatcher: buscar y descargar videos de la cámara por red local.
        Se abre el navegador solo. La primera vez te pide crear un usuario.
     2) Convertir videos de la tarjeta microSD a MP4.
        Los MP4 quedan en el Escritorio, junto con hashes_sha256.csv.
   Atajo: arrastrá la carpeta de la tarjeta SD encima de videoWatcher.exe.
3. Si Windows muestra "Windows protegió su PC": tocá "Más información" y
   después "Ejecutar de todas formas". Pasa porque el programa no está firmado.

Tus datos quedan en las carpetas "data" y "downloads", dentro de esta carpeta.
La guía completa está en LEEME.md (se abre con el Bloc de notas).
"@

$zip = "$dist\videoWatcher_portable.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $out -DestinationPath $zip
Write-Host "Portable folder: $out"
Write-Host "Zip:             $zip ($([math]::Round((Get-Item $zip).Length / 1MB, 1)) MB)"
