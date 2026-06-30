#
# Protokollierungsassistenz - Setup Script for Windows
# Intelligentes Setup-Skript fuer nicht-technische Benutzer
#
# Verwendung:
#   .\setup.ps1           # Anwendung starten (Standard)
#   .\setup.ps1 stop      # Anwendung stoppen
#   .\setup.ps1 status    # Status anzeigen
#   .\setup.ps1 restart   # Anwendung neu starten
#   .\setup.ps1 logs      # Live-Logs anzeigen
#   .\setup.ps1 cleanup   # Alle Daten loeschen und neu starten
#   .\setup.ps1 help      # Hilfe anzeigen
#

param(
    [Parameter(Position=0)]
    [string]$Command = "start"
)

$ErrorActionPreference = "Continue"

# Docker images. Defaults keep the established deployment path; set
# PROTOKOLL_IMAGE_TAG=v1.2.3 to pin application images to a release tag.
$IMAGE_BASE = "ghcr.io/aihpi/pilotproject-protokollierungsassistenz"
$PROTOKOLL_IMAGE_TAG = $env:PROTOKOLL_IMAGE_TAG
$script:PROTOKOLL_PULL_POLICY = if ($env:PROTOKOLL_PULL_POLICY) { $env:PROTOKOLL_PULL_POLICY } else { "missing" }

if ($PROTOKOLL_IMAGE_TAG) {
    $FRONTEND_IMAGE = if ($env:FRONTEND_IMAGE) { $env:FRONTEND_IMAGE } else { "${IMAGE_BASE}/frontend:${PROTOKOLL_IMAGE_TAG}" }
    $BACKEND_CPU_IMAGE = if ($env:BACKEND_IMAGE) { $env:BACKEND_IMAGE } else { "${IMAGE_BASE}/backend:${PROTOKOLL_IMAGE_TAG}" }
    $BACKEND_GPU_IMAGE = if ($env:BACKEND_GPU_IMAGE) { $env:BACKEND_GPU_IMAGE } else { "${IMAGE_BASE}/backend:${PROTOKOLL_IMAGE_TAG}-gpu" }
} else {
    $FRONTEND_IMAGE = if ($env:FRONTEND_IMAGE) { $env:FRONTEND_IMAGE } else { "${IMAGE_BASE}/frontend:latest" }
    $BACKEND_CPU_IMAGE = if ($env:BACKEND_IMAGE) { $env:BACKEND_IMAGE } else { "${IMAGE_BASE}/backend:cpu-latest" }
    $BACKEND_GPU_IMAGE = if ($env:BACKEND_GPU_IMAGE) { $env:BACKEND_GPU_IMAGE } else { "${IMAGE_BASE}/backend:gpu-latest" }
}

$OLLAMA_IMAGE_TAG = if ($env:OLLAMA_IMAGE_TAG) { $env:OLLAMA_IMAGE_TAG } else { "latest" }
$OLLAMA_IMAGE = if ($env:OLLAMA_IMAGE) { $env:OLLAMA_IMAGE } else { "ollama/ollama:${OLLAMA_IMAGE_TAG}" }
$OLLAMA_MODEL = if ($env:LLM_MODEL) { $env:LLM_MODEL } else { "qwen3:8b" }

$env:FRONTEND_IMAGE = $FRONTEND_IMAGE
$env:BACKEND_IMAGE = $BACKEND_CPU_IMAGE
$env:BACKEND_GPU_IMAGE = $BACKEND_GPU_IMAGE
$env:OLLAMA_IMAGE = $OLLAMA_IMAGE

# Ports used by the application
$PORT_FRONTEND = 3000
$PORT_BACKEND = 8010
$PORT_OLLAMA = 11434

# Global state
$script:USE_GPU = $false
$script:MissingItems = @()

# Print colored messages (German)
function Write-Info { Write-Host "[INFO] " -ForegroundColor Blue -NoNewline; Write-Host $args }
function Write-Success { Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $args }
function Write-Warn { Write-Host "[WARNUNG] " -ForegroundColor Yellow -NoNewline; Write-Host $args }
function Write-Err { Write-Host "[FEHLER] " -ForegroundColor Red -NoNewline; Write-Host $args }

# Get script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

#######################################
# Show help message
#######################################
function Show-Help {
    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  Protokollierungsassistenz - Hilfe" -ForegroundColor Cyan
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Verwendung: .\setup.ps1 [BEFEHL]"
    Write-Host ""
    Write-Host "Befehle:"
    Write-Host "  (ohne)     Anwendung starten oder fortsetzen"
    Write-Host "  stop       Anwendung stoppen"
    Write-Host "  status     Status der Dienste anzeigen"
    Write-Host "  restart    Anwendung neu starten"
    Write-Host "  logs       Live-Logs anzeigen (Strg+C zum Beenden)"
    Write-Host "  cleanup    Alle Daten loeschen und neu starten"
    Write-Host "  help       Diese Hilfe anzeigen"
    Write-Host ""
    Write-Host "Optionale Umgebungsvariablen:"
    Write-Host "  PROTOKOLL_IMAGE_TAG=v1.2.3       Stabile App-Images verwenden"
    Write-Host "  PROTOKOLL_PULL_POLICY=missing    Pull-Verhalten: missing, always, never"
    Write-Host "  FRONTEND_IMAGE/BACKEND_IMAGE/... Vollstaendige Image-Referenzen setzen"
    Write-Host ""
    Write-Host "Beispiele:"
    Write-Host "  .\setup.ps1           # Normale Installation/Start"
    Write-Host "  `$env:PROTOKOLL_IMAGE_TAG='v1.2.3'; .\setup.ps1"
    Write-Host "  .\setup.ps1 status    # Pruefen ob alles laeuft"
    Write-Host "  .\setup.ps1 logs      # Fehlersuche mit Logs"
    Write-Host ""
}

#######################################
# Pull images according to policy
#######################################
function Invoke-PullImages {
    switch ($script:PROTOKOLL_PULL_POLICY.ToLower()) {
        "always" {
            Write-Info "Pruefe auf Aktualisierungen (Pull-Policy: always)..."
        }
        "missing" {
            if ($script:MissingItems.Count -eq 0) {
                Write-Info "Lokale Images vorhanden; ueberspringe Pull (PROTOKOLL_PULL_POLICY=missing)."
                return
            }
            Write-Info "Lade fehlende Images (Pull-Policy: missing)..."
        }
        "never" {
            Write-Info "Ueberspringe Image-Pull (PROTOKOLL_PULL_POLICY=never)."
            return
        }
        default {
            Write-Warn "Unbekannte PROTOKOLL_PULL_POLICY '$script:PROTOKOLL_PULL_POLICY'; verwende 'missing'."
            if ($script:MissingItems.Count -eq 0) {
                return
            }
        }
    }

    if ($script:USE_GPU) {
        docker compose -f docker-compose.yml -f docker-compose.gpu.yml pull 2>&1 | Out-Null
    } else {
        docker compose pull 2>&1 | Out-Null
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Konnte Images nicht aktualisieren - verwende lokale Images"
    }
}

#######################################
# Check if Docker is installed and running
#######################################
function Test-Docker {
    Write-Info "Ueberpruefe Docker-Installation..."

    $dockerInstalled = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerInstalled) {
        Write-Err "Docker ist nicht installiert!"
        Write-Host ""
        Write-Host "Bitte installieren Sie Docker Desktop:"
        Write-Host "  https://docs.docker.com/desktop/install/windows-install/"
        Write-Host ""
        return $false
    }

    try {
        $null = docker info 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Docker not running"
        }
    } catch {
        Write-Err "Docker ist installiert, aber nicht gestartet!"
        Write-Host ""
        Write-Host "Bitte starten Sie Docker Desktop und versuchen Sie es erneut."
        return $false
    }

    Write-Success "Docker ist installiert und laeuft"
    return $true
}

#######################################
# Check if a Docker image exists locally
#######################################
function Test-ImageExists {
    param([string]$ImageName)

    try {
        $null = docker image inspect $ImageName 2>&1
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

#######################################
# Check if Ollama model is downloaded
#######################################
function Test-OllamaModelExists {
    $volumeName = (Split-Path -Leaf $ScriptDir) + "_ollama_data"

    try {
        $null = docker volume inspect $volumeName 2>&1
        if ($LASTEXITCODE -eq 0) {
            # Volume exists, assume model might be downloaded
            $volumeInfo = docker system df -v 2>&1 | Select-String $volumeName
            if ($volumeInfo -and $volumeInfo -notmatch "0B") {
                return $true
            }
        }
    } catch {
        # Volume doesn't exist
    }
    return $false
}

#######################################
# Smart disk space check
#######################################
function Test-DiskSpace {
    Write-Info "Ueberpruefe verfuegbaren Speicherplatz..."

    # Calculate required space based on what's already downloaded
    $requiredGB = 3  # Base runtime buffer
    $script:MissingItems = @()

    if (-not (Test-ImageExists $BACKEND_CPU_IMAGE) -and -not (Test-ImageExists $BACKEND_GPU_IMAGE)) {
        $requiredGB += 9
        $script:MissingItems += "Backend-Image (~9GB)"
    }

    if (-not (Test-ImageExists $FRONTEND_IMAGE)) {
        $requiredGB += 1
        $script:MissingItems += "Frontend-Image (~1GB)"
    }

    if (-not (Test-ImageExists $OLLAMA_IMAGE)) {
        $requiredGB += 2
        $script:MissingItems += "Ollama-Image (~2GB)"
    }

    if (-not (Test-OllamaModelExists)) {
        $requiredGB += 5
        $script:MissingItems += "Sprachmodell (~5GB)"
    }

    # Get available disk space
    $drive = (Get-Location).Drive.Name
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='${drive}:'" -ErrorAction SilentlyContinue
    if (-not $disk) {
        $disk = Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='${drive}:'"
    }
    $availableGB = [math]::Floor($disk.FreeSpace / 1GB)

    if ($script:MissingItems.Count -eq 0) {
        Write-Success "Alle Images bereits heruntergeladen"
        Write-Success "Verfuegbarer Speicherplatz: ${availableGB}GB (nur ~3GB benoetigt)"
        return $true
    }

    Write-Host "  Noch herunterzuladen:"
    foreach ($item in $script:MissingItems) {
        Write-Host "    - $item"
    }
    Write-Host ""

    if ($availableGB -lt $requiredGB) {
        Write-Err "Nicht genuegend Speicherplatz!"
        Write-Host ""
        Write-Host "  Verfuegbar: ${availableGB}GB"
        Write-Host "  Benoetigt:  ${requiredGB}GB"
        Write-Host ""
        Write-Host "Bitte geben Sie Speicherplatz frei und versuchen Sie es erneut."
        return $false
    }

    Write-Success "Speicherplatz OK (${availableGB}GB verfuegbar, ~${requiredGB}GB benoetigt)"
    return $true
}

#######################################
# Check RAM
#######################################
function Test-RAM {
    Write-Info "Ueberpruefe verfuegbaren Arbeitsspeicher..."

    $computerSystem = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
    if (-not $computerSystem) {
        $computerSystem = Get-WmiObject Win32_ComputerSystem
    }
    $totalRAMGB = [math]::Floor($computerSystem.TotalPhysicalMemory / 1GB)

    if ($totalRAMGB -lt 8) {
        Write-Warn "Wenig Arbeitsspeicher erkannt (${totalRAMGB}GB). Empfohlen: 8GB+"
        Write-Host "  Die Anwendung koennte langsam laufen."
    } else {
        Write-Success "Arbeitsspeicher OK (${totalRAMGB}GB verfuegbar)"
    }
    return $true
}

#######################################
# Check if a port is in use
#######################################
function Test-PortInUse {
    param([int]$Port)

    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return $null -ne $connection
    } catch {
        return $false
    }
}

#######################################
# Get process using a port
#######################################
function Get-PortProcess {
    param([int]$Port)

    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($connection) {
            $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
            if ($process) {
                return "$($process.ProcessName) (PID: $($process.Id))"
            }
        }
    } catch {
        # Ignore errors
    }
    return "Unbekannter Prozess"
}

#######################################
# Kill process using a port
#######################################
function Stop-PortProcess {
    param([int]$Port)

    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($connection) {
            Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
            return $true
        }
    } catch {
        # Ignore errors
    }
    return $false
}

#######################################
# Check for port conflicts
#######################################
function Test-Ports {
    Write-Info "Ueberpruefe Port-Verfuegbarkeit..."

    $conflicts = @()
    $conflictPorts = @()

    # Check each port (but ignore if our own containers are using them)
    foreach ($port in @($PORT_FRONTEND, $PORT_BACKEND, $PORT_OLLAMA)) {
        if (Test-PortInUse $port) {
            # Check if it's our own Docker container
            $isOurContainer = $false
            $composePs = docker compose ps 2>&1
            if ($composePs -match "0.0.0.0:${port}->") {
                $isOurContainer = $true
            }

            if (-not $isOurContainer) {
                $processInfo = Get-PortProcess $port
                $conflicts += "Port $port`: $processInfo"
                $conflictPorts += $port
            }
        }
    }

    if ($conflicts.Count -eq 0) {
        Write-Success "Alle Ports verfuegbar ($PORT_FRONTEND, $PORT_BACKEND, $PORT_OLLAMA)"
        return $true
    }

    Write-Warn "Port-Konflikte erkannt!"
    Write-Host ""
    foreach ($conflict in $conflicts) {
        Write-Host "  - $conflict"
    }
    Write-Host ""

    $response = Read-Host "Sollen die Ports automatisch freigegeben werden? (j/N)"

    if ($response -match "^[Jj]$") {
        foreach ($port in $conflictPorts) {
            Write-Info "Gebe Port $port frei..."
            if (Stop-PortProcess $port) {
                Write-Success "Port $port freigegeben"
            } else {
                Write-Err "Konnte Port $port nicht freigeben"
                Write-Host "  Bitte beenden Sie den Prozess manuell und versuchen Sie es erneut."
                return $false
            }
        }
        Start-Sleep -Seconds 1  # Give processes time to release ports
        return $true
    } else {
        Write-Host ""
        Write-Host "Bitte beenden Sie die konfliktierenden Prozesse manuell im Task-Manager."
        return $false
    }
}

#######################################
# Check for existing installation
#######################################
function Test-ExistingInstallation {
    # Check if docker compose containers exist
    $containers = docker compose ps -q 2>&1

    if (-not $containers -or $containers.Count -eq 0) {
        return $true  # No existing installation, continue
    }

    # Check container states
    $runningContainers = docker compose ps --status running -q 2>&1
    $runningCount = if ($runningContainers) { @($runningContainers).Count } else { 0 }

    $composePs = docker compose ps 2>&1
    $unhealthyCount = ($composePs | Select-String "unhealthy").Count

    $exitedContainers = docker compose ps --status exited -q 2>&1
    $exitedCount = if ($exitedContainers) { @($exitedContainers).Count } else { 0 }

    # All healthy and running
    if ($runningCount -ge 3 -and $unhealthyCount -eq 0) {
        Write-Host ""
        Write-Host "Die Anwendung laeuft bereits!" -ForegroundColor Green
        Write-Host ""
        Write-Host "  Frontend: http://localhost:$PORT_FRONTEND"
        Write-Host ""
        Write-Host "Optionen:"
        Write-Host "  1. Browser oeffnen"
        Write-Host "  2. Anwendung neu starten"
        Write-Host "  3. Anwendung stoppen"
        Write-Host "  4. Status anzeigen"
        Write-Host "  5. Nichts tun (beenden)"
        Write-Host ""
        $choice = Read-Host "Ihre Wahl (1/2/3/4/5)"

        switch ($choice) {
            "1" {
                Start-Process "http://localhost:$PORT_FRONTEND"
                exit 0
            }
            "2" {
                Invoke-Restart
                exit 0
            }
            "3" {
                Invoke-Stop
                exit 0
            }
            "4" {
                Show-Status
                exit 0
            }
            default {
                exit 0
            }
        }
    }

    # Some containers exist but not all healthy - partial/failed installation
    if ($exitedCount -gt 0 -or $unhealthyCount -gt 0 -or $runningCount -lt 3) {
        Write-Host ""
        Write-Warn "Es wurde eine unvollstaendige Installation gefunden!"
        Write-Host ""
        Write-Host "Container-Status:"
        docker compose ps 2>&1 | Select-Object -First 10
        Write-Host ""
        Write-Host "Optionen:"
        Write-Host "  1. Aufraeumen und neu starten (empfohlen)"
        Write-Host "  2. Versuchen, bestehende Container zu reparieren"
        Write-Host "  3. Abbrechen"
        Write-Host ""
        $choice = Read-Host "Ihre Wahl (1/2/3)"

        switch ($choice) {
            "1" {
                Write-Info "Raeume bestehende Container auf..."
                docker compose down --remove-orphans 2>&1 | Out-Null
                Write-Success "Aufgeraeumt. Starte neu..."
                return $true  # Continue with fresh setup
            }
            "2" {
                Write-Info "Versuche Container zu reparieren..."
                docker compose up -d 2>&1 | Out-Null
                Wait-ForServices
                exit 0
            }
            default {
                exit 0
            }
        }
    }

    return $true
}

#######################################
# Check for NVIDIA GPU
#######################################
function Test-GPU {
    $script:USE_GPU = $false

    Write-Info "Ueberpruefe NVIDIA GPU..."

    # Step 1: Detect NVIDIA GPU via Windows device manager (reliable, no nvidia-smi needed)
    $nvidiaGPU = $null
    $nvidiaGPU = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
    if (-not $nvidiaGPU) {
        $nvidiaGPU = Get-WmiObject Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
    }

    if (-not $nvidiaGPU) {
        Write-Info "Keine NVIDIA GPU im System gefunden, verwende CPU-Modus"
        return
    }

    Write-Success "NVIDIA GPU erkannt: $($nvidiaGPU.Name)"

    # Step 2: Verify NVIDIA driver works via nvidia-smi
    $nvidiaSmiCmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvidiaSmiCmd) {
        Write-Host "  Pruefe NVIDIA-Treiber ($($nvidiaSmiCmd.Source))..."
        $tempOut = Join-Path $env:TEMP "nvsmi_out.txt"
        $tempErr = Join-Path $env:TEMP "nvsmi_err.txt"
        try {
            $proc = Start-Process -FilePath $nvidiaSmiCmd.Source -NoNewWindow -Wait -PassThru -RedirectStandardOutput $tempOut -RedirectStandardError $tempErr
            if ($proc.ExitCode -ne 0) {
                $errMsg = ""
                if (Test-Path $tempErr) { $errMsg = (Get-Content $tempErr -Raw -ErrorAction SilentlyContinue).Trim() }
                Write-Warn "nvidia-smi fehlgeschlagen (Exit-Code: $($proc.ExitCode))"
                if ($errMsg) { Write-Host "  $errMsg" }
                Write-Host "  Treiber-Update empfohlen: https://www.nvidia.com/Download/index.aspx"
            } else {
                Write-Success "NVIDIA-Treiber OK"
            }
        } catch {
            Write-Warn "nvidia-smi konnte nicht ausgefuehrt werden: $($_.Exception.Message)"
        } finally {
            Remove-Item $tempOut -ErrorAction SilentlyContinue
            Remove-Item $tempErr -ErrorAction SilentlyContinue
        }
    } else {
        Write-Warn "nvidia-smi nicht gefunden - Treiberstatus konnte nicht geprueft werden"
    }

    # Step 3: Ask user about GPU mode
    Write-Host ""
    Write-Host "GPU-Modus bietet deutlich schnellere Transkription."
    $response = Read-Host "Moechten Sie den GPU-Modus verwenden? (j/N)"

    if ($response -match "^[Jj]$") {
        $dockerInfo = docker info 2>&1
        if ($dockerInfo -match "nvidia") {
            $script:USE_GPU = $true
            Write-Success "GPU-Modus aktiviert"
        } else {
            Write-Warn "NVIDIA Container Toolkit moeglicherweise nicht konfiguriert"
            Write-Host "  Fuer GPU-Modus stellen Sie sicher, dass Docker Desktop GPU-Unterstuetzung hat."
            Write-Host "  Siehe: https://docs.docker.com/desktop/gpu/"
            Write-Host ""
            $continue = Read-Host "GPU-Modus trotzdem versuchen? (j/N)"
            if ($continue -match "^[Jj]$") {
                $script:USE_GPU = $true
            } else {
                Write-Host "  Fahre mit CPU-Modus fort..."
            }
        }
    }
}

#######################################
# Wait for services to be ready
#######################################
function Wait-ForServices {
    Write-Host ""
    Write-Info "Warte auf Dienste..."
    Write-Host "Das System laedt KI-Modelle. Dies kann einige Minuten dauern."
    Write-Host ""

    $maxWait = 600  # 10 minutes
    $waitCount = 0
    $ready = $false

    while ($waitCount -lt $maxWait) {
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:$PORT_BACKEND/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            # Service not ready yet
        }

        # Show progress every 15 seconds
        if ($waitCount % 15 -eq 0) {
            Write-Host "  Laedt noch... (${waitCount}s vergangen)"
        }

        Start-Sleep -Seconds 1
        $waitCount++
    }

    if (-not $ready) {
        Write-Host ""
        Write-Err "Dienste konnten nicht gestartet werden!"
        Write-Host ""
        Show-FailureDiagnostics
        return $false
    }

    Write-Host ""
    Write-Success "Anwendung ist bereit!"
    Show-SuccessMessage
    Start-Process "http://localhost:$PORT_FRONTEND"
    return $true
}

#######################################
# Show failure diagnostics
#######################################
function Show-FailureDiagnostics {
    Write-Host "========== Fehlerdiagnose ==========" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Container-Status:"
    docker compose ps 2>&1
    Write-Host ""
    Write-Host "Letzte Log-Eintraege:"
    docker compose logs --tail=20 2>&1
    Write-Host ""
    Write-Host "========== Moegliche Ursachen ==========" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1. Nicht genuegend Arbeitsspeicher"
    Write-Host "   -> Schliessen Sie andere Programme"
    Write-Host "   -> Erhoehen Sie Docker-Speicher in Docker Desktop Einstellungen"
    Write-Host ""
    Write-Host "2. Netzwerkprobleme"
    Write-Host "   -> Ueberpruefen Sie Ihre Internetverbindung"
    Write-Host "   -> Versuchen Sie: docker compose pull"
    Write-Host ""
    Write-Host "3. Docker-Ressourcen"
    Write-Host "   -> Docker Desktop -> Einstellungen -> Resources"
    Write-Host "   -> Empfohlen: Mindestens 8GB RAM, 4 CPUs"
    Write-Host ""
    Write-Host "Naechste Schritte:"
    Write-Host "  1. .\setup.ps1 logs      # Detaillierte Logs anzeigen"
    Write-Host "  2. .\setup.ps1 cleanup   # Alles loeschen und neu starten"
    Write-Host ""
}

#######################################
# Show success message
#######################################
function Show-SuccessMessage {
    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  Installation erfolgreich!" -ForegroundColor Cyan
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Oeffnen Sie Ihren Browser:"
    Write-Host "  http://localhost:$PORT_FRONTEND" -ForegroundColor Green
    Write-Host ""
    Write-Host "Nuetzliche Befehle:"
    Write-Host "  .\setup.ps1 stop      Anwendung stoppen"
    Write-Host "  .\setup.ps1 status    Status anzeigen"
    Write-Host "  .\setup.ps1 logs      Logs anzeigen"
    Write-Host ""
}

#######################################
# Start the application
#######################################
function Invoke-Start {
    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  Protokollierungsassistenz - Setup" -ForegroundColor Cyan
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host ""

    # Pre-flight checks
    if (-not (Test-Docker)) {
        Read-Host "Druecken Sie Enter zum Beenden"
        exit 1
    }

    if (-not (Test-ExistingInstallation)) {
        exit 1
    }

    if (-not (Test-DiskSpace)) {
        Read-Host "Druecken Sie Enter zum Beenden"
        exit 1
    }

    Test-RAM | Out-Null

    if (-not (Test-Ports)) {
        Read-Host "Druecken Sie Enter zum Beenden"
        exit 1
    }

    Test-GPU

    # Create uploads directory
    New-Item -ItemType Directory -Force -Path "uploads" | Out-Null

    # Start the application
    Write-Host ""
    Write-Info "Starte die Anwendung..."

    if ($script:MissingItems.Count -gt 0) {
        Write-Host "Downloads koennen einige Minuten dauern."
    }
    Write-Host ""

    Invoke-PullImages
    Write-Host ""

    if ($script:USE_GPU) {
        Write-Info "Starte im GPU-Modus..."
        docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
    } else {
        Write-Info "Starte im CPU-Modus..."
        docker compose up -d
    }

    $success = Wait-ForServices

    Write-Host ""
    Read-Host "Druecken Sie Enter zum Schliessen"
}

#######################################
# Stop the application
#######################################
function Invoke-Stop {
    Write-Host ""
    Write-Info "Stoppe die Anwendung..."
    docker compose down
    Write-Success "Anwendung gestoppt"
    Write-Host ""
}

#######################################
# Show status
#######################################
function Show-Status {
    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  Protokollierungsassistenz - Status" -ForegroundColor Cyan
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host ""

    # Check if containers exist
    $containers = docker compose ps -q 2>&1
    if (-not $containers -or $containers.Count -eq 0) {
        Write-Host "Die Anwendung ist nicht gestartet."
        Write-Host ""
        Write-Host "Starten mit: .\setup.ps1"
        return
    }

    Write-Host "Container-Status:"
    docker compose ps
    Write-Host ""

    # Check health
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$PORT_BACKEND/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Host "Backend: Erreichbar" -ForegroundColor Green
        } else {
            Write-Host "Backend: Nicht erreichbar" -ForegroundColor Red
        }
    } catch {
        Write-Host "Backend: Nicht erreichbar" -ForegroundColor Red
    }

    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$PORT_FRONTEND" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        Write-Host "Frontend: Erreichbar" -ForegroundColor Green
    } catch {
        Write-Host "Frontend: Nicht erreichbar" -ForegroundColor Red
    }

    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$PORT_OLLAMA/api/tags" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        Write-Host "Ollama: Erreichbar" -ForegroundColor Green
    } catch {
        Write-Host "Ollama: Nicht erreichbar" -ForegroundColor Red
    }

    Write-Host ""
    Write-Host "URL: http://localhost:$PORT_FRONTEND"
    Write-Host ""
}

#######################################
# Restart the application
#######################################
function Invoke-Restart {
    Write-Host ""
    Write-Info "Starte die Anwendung neu..."
    docker compose restart
    Write-Success "Anwendung neu gestartet"
    Write-Host ""

    # Brief wait and check
    Start-Sleep -Seconds 3
    Show-Status
}

#######################################
# Show logs
#######################################
function Show-Logs {
    Write-Host ""
    Write-Info "Zeige Live-Logs (Strg+C zum Beenden)..."
    Write-Host ""
    docker compose logs -f
}

#######################################
# Cleanup everything
#######################################
function Invoke-Cleanup {
    Write-Host ""
    Write-Warn "ACHTUNG: Dies loescht alle Anwendungsdaten!"
    Write-Host ""
    Write-Host "Folgendes wird entfernt:"
    Write-Host "  - Alle Docker-Container"
    Write-Host "  - Alle Volumes (inkl. heruntergeladener Modelle)"
    Write-Host "  - Hochgeladene Dateien bleiben erhalten (uploads/)"
    Write-Host ""
    $response = Read-Host "Sind Sie sicher? (j/N)"

    if ($response -notmatch "^[Jj]$") {
        Write-Host "Abgebrochen."
        return
    }

    Write-Info "Raeume auf..."
    docker compose down -v --remove-orphans 2>&1 | Out-Null
    Write-Success "Aufraeumen abgeschlossen"
    Write-Host ""

    $response = Read-Host "Moechten Sie die Anwendung jetzt neu installieren? (J/n)"

    if ($response -notmatch "^[Nn]$") {
        Invoke-Start
    }
}

#######################################
# Main entry point
#######################################
switch ($Command.ToLower()) {
    "start" {
        Invoke-Start
    }
    "" {
        Invoke-Start
    }
    "stop" {
        Invoke-Stop
    }
    "status" {
        Show-Status
    }
    "restart" {
        Invoke-Restart
    }
    "logs" {
        Show-Logs
    }
    "cleanup" {
        Invoke-Cleanup
    }
    "help" {
        Show-Help
    }
    "--help" {
        Show-Help
    }
    "-h" {
        Show-Help
    }
    default {
        Write-Err "Unbekannter Befehl: $Command"
        Show-Help
        exit 1
    }
}
