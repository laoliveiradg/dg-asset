$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$spec = Join-Path $projectRoot "Asset.spec"
$executable = Join-Path $projectRoot "dist\Asset.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Ambiente de build ausente. Crie .venv e instale o extra [build]."
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller falhou com código $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $executable)) {
    throw "O executável esperado não foi criado: $executable"
}

$artifact = Get-Item -LiteralPath $executable
Write-Output ("Criado: {0} ({1:N0} bytes)" -f $artifact.FullName, $artifact.Length)
