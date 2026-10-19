param(
    [string]$Version
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot

try {
    $config = Get-Content ".\config.py" -Raw
    if ($config -notmatch '(?m)^APP_VERSION\s*=\s*"([^"]+)"') {
        throw "No se encontró APP_VERSION en config.py."
    }

    $configuredVersion = $Matches[1]
    if (-not $Version) {
        $Version = $configuredVersion
    }
    if ($Version -ne $configuredVersion) {
        throw "La versión solicitada ($Version) no coincide con APP_VERSION ($configuredVersion)."
    }

    $tag = "v$Version"
    $tagExists = $null -ne (git tag --list $tag)
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudieron consultar las etiquetas de Git."
    }

    if ($tagExists) {
        git diff --quiet $tag --
        $trackedChanges = $LASTEXITCODE -ne 0
        $untrackedFiles = @(git ls-files --others --exclude-standard)
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudieron comprobar los archivos nuevos."
        }
        if (-not $trackedChanges -and $untrackedFiles.Count -eq 0) {
            Write-Host "No hay cambios desde $tag. No se genera otro ejecutable."
            exit 0
        }
        throw "La versión $tag ya existe y hay cambios. Actualiza APP_VERSION antes de preparar otro release."
    }

    $tagList = @(git tag --list "v*" --sort=-version:refname)
    $tagListExitCode = $LASTEXITCODE
    if ($tagListExitCode -ne 0) {
        throw "No se pudieron consultar las etiquetas de Git."
    }
    $latestTag = $tagList | Select-Object -First 1

    $hasChanges = $false
    if ($latestTag) {
        git diff --quiet "$latestTag" HEAD --
        $comparisonExitCode = $LASTEXITCODE
        if ($comparisonExitCode -gt 1) {
            throw "No se pudieron comparar los cambios con $latestTag."
        }
        $hasChanges = $comparisonExitCode -ne 0

        git diff --quiet HEAD --
        $workingTreeExitCode = $LASTEXITCODE
        if ($workingTreeExitCode -gt 1) {
            throw "No se pudieron comprobar los cambios locales."
        }
        $hasChanges = $hasChanges -or ($workingTreeExitCode -ne 0)
    }
    else {
        git diff --quiet HEAD --
        $workingTreeExitCode = $LASTEXITCODE
        if ($workingTreeExitCode -gt 1) {
            throw "No se pudieron comprobar los cambios locales."
        }
        $hasChanges = $workingTreeExitCode -ne 0
    }

    $untrackedFiles = @(git ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudieron comprobar los archivos nuevos."
    }
    $hasChanges = $hasChanges -or ($untrackedFiles.Count -gt 0)

    if (-not $hasChanges) {
        Write-Host "No hay cambios desde el último release. No se genera otro ejecutable."
        exit 0
    }

    if (Test-Path ".\venv\Scripts\python.exe") {
        $python = (Resolve-Path ".\venv\Scripts\python.exe").Path
    }
    else {
        $python = "python"
    }

    Write-Host "Ejecutando pruebas..."
    & $python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Las pruebas han fallado. Se cancela la preparación del release."
    }

    Write-Host "Comprobando sintaxis..."
    & $python -m py_compile aules_client.py config.py downloader.py file_manager.py gui.py main.py
    if ($LASTEXITCODE -ne 0) {
        throw "Falló la comprobación de sintaxis. Se cancela la preparación del release."
    }

    Write-Host "Compilando el ejecutable v$Version..."
    & $python -m PyInstaller --clean --noconfirm --onefile --windowed `
        --name Aules_PDF_Synchronizer --icon image.ico `
        --add-data "image.ico;." --collect-all selenium `
        --collect-all webdriver_manager --distpath release --workpath build `
        main.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller no pudo crear el ejecutable."
    }

    $executable = Resolve-Path ".\release\Aules_PDF_Synchronizer.exe"
    $hash = Get-FileHash $executable.Path -Algorithm SHA256
    Write-Host ""
    Write-Host "Release v$Version preparado. No se ha creado ningún commit ni se ha publicado en GitHub."
    Write-Host "Ejecutable: $($executable.Path)"
    Write-Host "SHA256: $($hash.Hash)"
}
finally {
    Pop-Location
}
