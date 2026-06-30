#!/bin/bash
#
# Protokollierungsassistenz - Setup Script for macOS/Linux
# Intelligentes Setup-Skript fuer nicht-technische Benutzer
#
# Verwendung:
# ./setup.sh           # Anwendung starten (Standard)
# ./setup.sh stop      # Anwendung stoppen
# ./setup.sh status    # Status anzeigen
# ./setup.sh restart   # Anwendung neu starten
# ./setup.sh logs      # Live-Logs anzeigen
# ./setup.sh cleanup   # Alle Daten loeschen und neu starten
# ./setup.sh help      # Hilfe anzeigen
#

# Exit on error (disabled for interactive sections)
set +e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Docker images. Defaults keep the established deployment path; set
# PROTOKOLL_IMAGE_TAG=v1.2.3 to pin application images to a release tag.
IMAGE_BASE="ghcr.io/aihpi/pilotproject-protokollierungsassistenz"
PROTOKOLL_IMAGE_TAG="${PROTOKOLL_IMAGE_TAG:-}"
PROTOKOLL_PULL_POLICY="${PROTOKOLL_PULL_POLICY:-missing}" # missing|always|never

if [ -n "$PROTOKOLL_IMAGE_TAG" ]; then
    FRONTEND_IMAGE="${FRONTEND_IMAGE:-${IMAGE_BASE}/frontend:${PROTOKOLL_IMAGE_TAG}}"
    BACKEND_CPU_IMAGE="${BACKEND_IMAGE:-${IMAGE_BASE}/backend:${PROTOKOLL_IMAGE_TAG}}"
    BACKEND_GPU_IMAGE="${BACKEND_GPU_IMAGE:-${IMAGE_BASE}/backend:${PROTOKOLL_IMAGE_TAG}-gpu}"
else
    FRONTEND_IMAGE="${FRONTEND_IMAGE:-${IMAGE_BASE}/frontend:latest}"
    BACKEND_CPU_IMAGE="${BACKEND_IMAGE:-${IMAGE_BASE}/backend:cpu-latest}"
    BACKEND_GPU_IMAGE="${BACKEND_GPU_IMAGE:-${IMAGE_BASE}/backend:gpu-latest}"
fi

BACKEND_IMAGE="$BACKEND_CPU_IMAGE"
OLLAMA_IMAGE="${OLLAMA_IMAGE:-ollama/ollama:${OLLAMA_IMAGE_TAG:-latest}}"
OLLAMA_MODEL="${LLM_MODEL:-qwen3:8b}"

export FRONTEND_IMAGE BACKEND_IMAGE BACKEND_GPU_IMAGE OLLAMA_IMAGE

# Global state
MISSING_ITEMS=()

# Ports used by the application
PORT_FRONTEND=3000
PORT_BACKEND=8010
PORT_OLLAMA=11434

# Print colored messages (German)
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNUNG]${NC} $1"; }
error() { echo -e "${RED}[FEHLER]${NC} $1"; }

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

########################################
# Show help message
########################################
show_help() {
    echo ""
    echo -e "${CYAN}=============================================="
    echo "  Protokollierungsassistenz - Hilfe"
    echo -e "==============================================${NC}"
    echo ""
    echo "Verwendung: ./setup.sh [BEFEHL]"
    echo ""
    echo "Befehle:"
    echo "  (ohne)      Anwendung starten oder fortsetzen"
    echo "  stop        Anwendung stoppen"
    echo "  status      Status der Dienste anzeigen"
    echo "  restart     Anwendung neu starten"
    echo "  logs        Live-Logs anzeigen (Strg+C zum Beenden)"
    echo "  cleanup     Alle Daten loeschen und neu starten"
    echo "  help        Diese Hilfe anzeigen"
    echo ""
    echo "Optionale Umgebungsvariablen:"
    echo "  PROTOKOLL_IMAGE_TAG=v1.2.3       Stabile App-Images verwenden"
    echo "  PROTOKOLL_PULL_POLICY=missing    Pull-Verhalten: missing, always, never"
    echo "  FRONTEND_IMAGE/BACKEND_IMAGE/... Vollstaendige Image-Referenzen setzen"
    echo ""
    echo "Beispiele:"
    echo "  ./setup.sh          # Normale Installation/Start"
    echo "  PROTOKOLL_IMAGE_TAG=v1.2.3 ./setup.sh"
    echo "  ./setup.sh status   # Pruefen ob alles laeuft"
    echo "  ./setup.sh logs     # Fehlersuche mit Logs"
    echo ""
}

########################################
# Check if Docker is installed and running
########################################
check_docker() {
    info "Ueberpruefe Docker-Installation..."

    if ! command -v docker &> /dev/null; then
        error "Docker ist nicht installiert!"
        echo ""
        echo "Bitte installieren Sie Docker Desktop:"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo "  https://docs.docker.com/desktop/install/mac-install/"
        else
            echo "  https://docs.docker.com/desktop/install/linux-install/"
        fi
        echo ""
        return 1
    fi

    if ! docker info &> /dev/null; then
        error "Docker ist installiert, aber nicht gestartet!"
        echo ""
        echo "Bitte starten Sie Docker Desktop und versuchen Sie es erneut."
        return 1
    fi

    success "Docker ist installiert und laeuft"
    return 0
}

########################################
# Check if a Docker image exists locally
########################################
image_exists() {
    docker image inspect "$1" &> /dev/null
}

########################################
# Check if Ollama model is downloaded
########################################
ollama_model_exists() {
    local volume_name="${SCRIPT_DIR##*/}_ollama_data"
    # Check if volume exists and has data
    if docker volume inspect "$volume_name" &> /dev/null; then
        # Volume exists, check if model is likely downloaded (volume has data)
        local volume_size
        volume_size=$(docker system df -v 2>/dev/null | grep "$volume_name" | awk '{print $3}' | head -1)
        if [[ -n "$volume_size" && "$volume_size" != "0B" ]]; then
            return 0
        fi
    fi
    return 1
}

########################################
# Smart disk space check
########################################
check_disk_space() {
    info "Ueberpruefe verfuegbaren Speicherplatz..."

    # Calculate required space based on what's already downloaded
    local required_gb=3  # Base runtime buffer
    MISSING_ITEMS=()

    if ! image_exists "$BACKEND_CPU_IMAGE" && ! image_exists "$BACKEND_GPU_IMAGE"; then
        required_gb=$((required_gb + 9))
        MISSING_ITEMS+=("Backend-Image (~9GB)")
    fi

    if ! image_exists "$FRONTEND_IMAGE"; then
        required_gb=$((required_gb + 1))
        MISSING_ITEMS+=("Frontend-Image (~1GB)")
    fi

    if ! image_exists "$OLLAMA_IMAGE"; then
        required_gb=$((required_gb + 2))
        MISSING_ITEMS+=("Ollama-Image (~2GB)")
    fi

    if ! ollama_model_exists; then
        required_gb=$((required_gb + 5))
        MISSING_ITEMS+=("Sprachmodell (~5GB)")
    fi

    # Get available disk space
    local available_gb
    if [[ "$OSTYPE" == "darwin"* ]]; then
        available_gb=$(df -g . | tail -1 | awk '{print $4}')
    else
        available_gb=$(df -BG . | tail -1 | awk '{print $4}' | sed 's/G//')
    fi

    if [ ${#MISSING_ITEMS[@]} -eq 0 ]; then
        success "Alle Images bereits heruntergeladen"
        success "Verfuegbarer Speicherplatz: ${available_gb}GB (nur ~3GB benoetigt)"
        return 0
    fi

    echo "  Noch herunterzuladen:"
    for item in "${MISSING_ITEMS[@]}"; do
        echo "    - $item"
    done
    echo ""

    if [ "$available_gb" -lt "$required_gb" ]; then
        error "Nicht genuegend Speicherplatz!"
        echo ""
        echo "  Verfuegbar: ${available_gb}GB"
        echo "  Benoetigt:  ${required_gb}GB"
        echo ""
        echo "Bitte geben Sie Speicherplatz frei und versuchen Sie es erneut."
        return 1
    fi

    success "Speicherplatz OK (${available_gb}GB verfuegbar, ~${required_gb}GB benoetigt)"
    return 0
}

########################################
# Pull images according to policy
########################################
pull_images() {
    case "$PROTOKOLL_PULL_POLICY" in
        always)
            info "Pruefe auf Aktualisierungen (Pull-Policy: always)..."
            ;;
        missing)
            if [ ${#MISSING_ITEMS[@]} -eq 0 ]; then
                info "Lokale Images vorhanden; ueberspringe Pull (PROTOKOLL_PULL_POLICY=missing)."
                return 0
            fi
            info "Lade fehlende Images (Pull-Policy: missing)..."
            ;;
        never)
            info "Ueberspringe Image-Pull (PROTOKOLL_PULL_POLICY=never)."
            return 0
            ;;
        *)
            warn "Unbekannte PROTOKOLL_PULL_POLICY '$PROTOKOLL_PULL_POLICY'; verwende 'missing'."
            if [ ${#MISSING_ITEMS[@]} -eq 0 ]; then
                return 0
            fi
            ;;
    esac

    if [ "$USE_GPU" = true ]; then
        docker compose -f docker-compose.yml -f docker-compose.gpu.yml pull 2>/dev/null || warn "Konnte Images nicht aktualisieren - verwende lokale Images"
    else
        docker compose pull 2>/dev/null || warn "Konnte Images nicht aktualisieren - verwende lokale Images"
    fi
}

########################################
# Check RAM
########################################
check_ram() {
    info "Ueberpruefe verfuegbaren Arbeitsspeicher..."

    local total_ram_gb
    if [[ "$OSTYPE" == "darwin"* ]]; then
        total_ram_gb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
    else
        total_ram_gb=$(free -g | awk '/^Mem:/{print $2}')
    fi

    if [ "$total_ram_gb" -lt 8 ]; then
        warn "Wenig Arbeitsspeicher erkannt (${total_ram_gb}GB). Empfohlen: 8GB+"
        echo "  Die Anwendung koennte langsam laufen."
    else
        success "Arbeitsspeicher OK (${total_ram_gb}GB verfuegbar)"
    fi
    return 0
}

########################################
# Check if a port is in use
########################################
port_in_use() {
    local port=$1
    if [[ "$OSTYPE" == "darwin"* ]]; then
        lsof -i :"$port" -sTCP:LISTEN &> /dev/null
    else
        ss -tuln 2>/dev/null | grep -q ":${port} " || netstat -tuln 2>/dev/null | grep -q ":${port} "
    fi
}

########################################
# Get process using a port
########################################
get_port_process() {
    local port=$1
    if [[ "$OSTYPE" == "darwin"* ]]; then
        lsof -i :"$port" -sTCP:LISTEN 2>/dev/null | tail -1 | awk '{print $1 " (PID: " $2 ")"}'
    else
        # Try ss first, then netstat
        local pid
        pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)
        if [ -n "$pid" ]; then
            ps -p "$pid" -o comm= 2>/dev/null | head -1
            echo " (PID: $pid)"
        else
            echo "Unbekannter Prozess"
        fi
    fi
}

########################################
# Kill process using a port
########################################
kill_port_process() {
    local port=$1
    if [[ "$OSTYPE" == "darwin"* ]]; then
        local pid
        pid=$(lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
        if [ -n "$pid" ]; then
            kill -9 "$pid" 2>/dev/null
            return $?
        fi
    else
        local pid
        pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)
        if [ -n "$pid" ]; then
            kill -9 "$pid" 2>/dev/null
            return $?
        fi
    fi
    return 1
}

########################################
# Check for port conflicts
########################################
check_ports() {
    info "Ueberpruefe Port-Verfuegbarkeit..."

    local conflicts=()
    local conflict_ports=()

    # Check each port (but ignore if our own containers are using them)
    for port in $PORT_FRONTEND $PORT_BACKEND $PORT_OLLAMA; do
        if port_in_use "$port"; then
            # Check if it's our own Docker container
            local is_our_container=false
            if docker compose ps 2>/dev/null | grep -q "0.0.0.0:${port}->"; then
                is_our_container=true
            fi

            if [ "$is_our_container" = false ]; then
                local process_info
                process_info=$(get_port_process "$port")
                conflicts+=("Port $port: $process_info")
                conflict_ports+=("$port")
            fi
        fi
    done

    if [ ${#conflicts[@]} -eq 0 ]; then
        success "Alle Ports verfuegbar (${PORT_FRONTEND}, ${PORT_BACKEND}, ${PORT_OLLAMA})"
        return 0
    fi

    warn "Port-Konflikte erkannt!"
    echo ""
    for conflict in "${conflicts[@]}"; do
        echo "  - $conflict"
    done
    echo ""

    read -p "Sollen die Ports automatisch freigegeben werden? (j/N): " -n 1 -r
    echo ""

    if [[ $REPLY =~ ^[Jj]$ ]]; then
        for port in "${conflict_ports[@]}"; do
            info "Gebe Port $port frei..."
            if kill_port_process "$port"; then
                success "Port $port freigegeben"
            else
                error "Konnte Port $port nicht freigeben"
                echo "  Bitte beenden Sie den Prozess manuell und versuchen Sie es erneut."
                return 1
            fi
        done
        sleep 1  # Give processes time to release ports
        return 0
    else
        echo ""
        echo "Bitte beenden Sie die konfliktierenden Prozesse manuell:"
        for port in "${conflict_ports[@]}"; do
            echo "  - Port $port: kill \$(lsof -t -i:$port)"
        done
        return 1
    fi
}

########################################
# Check for existing installation
########################################
check_existing_installation() {
    # Check if docker compose containers exist
    local containers
    containers=$(docker compose ps -q 2>/dev/null)

    if [ -z "$containers" ]; then
        return 0  # No existing installation
    fi

    # Check container states
    local running_count
    local unhealthy_count
    local exited_count

    running_count=$(docker compose ps --status running -q 2>/dev/null | wc -l | tr -d ' ')
    unhealthy_count=$(docker compose ps 2>/dev/null | grep -c "unhealthy" || echo "0")
    exited_count=$(docker compose ps --status exited -q 2>/dev/null | wc -l | tr -d ' ')

    # All healthy and running
    if [ "$running_count" -ge 3 ] && [ "$unhealthy_count" -eq 0 ]; then
        echo ""
        echo -e "${GREEN}Die Anwendung laeuft bereits!${NC}"
        echo ""
        echo "  Frontend: http://localhost:${PORT_FRONTEND}"
        echo ""
        echo "Optionen:"
        echo "  1. Browser oeffnen"
        echo "  2. Anwendung neu starten"
        echo "  3. Anwendung stoppen"
        echo "  4. Status anzeigen"
        echo "  5. Nichts tun (beenden)"
        echo ""
        read -p "Ihre Wahl (1/2/3/4/5): " -n 1 -r
        echo ""

        case $REPLY in
            1)
                open_browser
                exit 0
                ;;
            2)
                do_restart
                exit $?
                ;;
            3)
                do_stop
                exit $?
                ;;
            4)
                do_status
                exit 0
                ;;
            5|*)
                exit 0
                ;;
        esac
    fi

    # Some containers exist but not all healthy - partial/failed installation
    if [ "$exited_count" -gt 0 ] || [ "$unhealthy_count" -gt 0 ] || [ "$running_count" -lt 3 ]; then
        echo ""
        warn "Es wurde eine unvollstaendige Installation gefunden!"
        echo ""
        echo "Container-Status:"
        docker compose ps 2>/dev/null | head -10
        echo ""
        echo "Optionen:"
        echo "  1. Aufraeumen und neu starten (empfohlen)"
        echo "  2. Versuchen, bestehende Container zu reparieren"
        echo "  3. Abbrechen"
        echo ""
        read -p "Ihre Wahl (1/2/3): " -n 1 -r
        echo ""

        case $REPLY in
            1)
                info "Raeume bestehende Container auf..."
                docker compose down --remove-orphans 2>/dev/null
                success "Aufgeraeumt. Starte neu..."
                return 0  # Continue with fresh setup
                ;;
            2)
                info "Versuche Container zu reparieren..."
                docker compose up -d 2>/dev/null
                wait_for_services
                exit $?
                ;;
            3|*)
                exit 0
                ;;
        esac
    fi

    return 0
}

########################################
# Check for NVIDIA GPU
########################################
check_gpu() {
    USE_GPU=false

    if [[ "$OSTYPE" == "darwin"* ]]; then
        info "macOS erkannt - verwende CPU-Modus (NVIDIA nicht unterstuetzt auf Mac)"
        return 0
    fi

    info "Ueberpruefe NVIDIA GPU..."

    if command -v nvidia-smi &> /dev/null; then
        local nvidia_output
        nvidia_output=$(nvidia-smi 2>&1)
        if [ $? -eq 0 ]; then
            success "NVIDIA GPU erkannt!"
            echo ""
            echo "GPU-Modus bietet deutlich schnellere Transkription."
            read -p "Moechten Sie den GPU-Modus verwenden? (j/N): " -n 1 -r
            echo ""

            if [[ $REPLY =~ ^[Jj]$ ]]; then
                # Check for NVIDIA Container Toolkit via multiple methods
                local nvidia_docker_found=false
                if docker info 2>/dev/null | grep -qi "nvidia"; then
                    nvidia_docker_found=true
                elif [ -f /etc/nvidia-container-runtime/config.toml ]; then
                    nvidia_docker_found=true
                elif [ -f /etc/docker/daemon.json ] && grep -q "nvidia" /etc/docker/daemon.json 2>/dev/null; then
                    nvidia_docker_found=true
                elif command -v nvidia-container-cli &> /dev/null; then
                    nvidia_docker_found=true
                fi

                if [ "$nvidia_docker_found" = true ]; then
                    USE_GPU=true
                    success "GPU-Modus aktiviert"
                else
                    warn "NVIDIA Container Toolkit nicht erkannt"
                    echo ""
                    echo "  nvidia-smi funktioniert, aber Docker kann die GPU nicht nutzen."
                    echo "  Bitte installieren Sie das NVIDIA Container Toolkit:"
                    echo ""
                    echo "  Ubuntu/Debian:"
                    echo "    sudo apt-get install -y nvidia-container-toolkit"
                    echo "    sudo systemctl restart docker"
                    echo ""
                    echo "  Weitere Informationen:"
                    echo "  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
                    echo ""
                    echo "  Fahre mit CPU-Modus fort..."
                fi
            fi
        else
            warn "nvidia-smi gefunden, aber fehlgeschlagen:"
            echo "  $nvidia_output" | head -3
            echo ""
            echo "  Moegliche Ursachen:"
            echo "  - Treiber-/Kernel-Version Mismatch (Neustart erforderlich)"
            echo "  - NVIDIA-Treiber nicht korrekt installiert"
            echo ""
            info "Fahre mit CPU-Modus fort..."
        fi
    else
        info "Keine NVIDIA GPU erkannt, verwende CPU-Modus"
    fi
}

########################################
# Wait for services to be ready
########################################
wait_for_services() {
    echo ""
    info "Warte auf Dienste..."
    echo "Das System laedt KI-Modelle. Dies kann einige Minuten dauern."
    echo ""

    local max_wait=600  # 10 minutes
    local wait_count=0

    while [ $wait_count -lt $max_wait ]; do
        if curl -s http://localhost:${PORT_BACKEND}/health > /dev/null 2>&1; then
            break
        fi

        # Show progress every 15 seconds
        if [ $((wait_count % 15)) -eq 0 ]; then
            echo "  Laedt noch... (${wait_count}s vergangen)"
        fi

        sleep 1
        wait_count=$((wait_count + 1))
    done

    if [ $wait_count -ge $max_wait ]; then
        echo ""
        error "Dienste konnten nicht gestartet werden!"
        echo ""
        show_failure_diagnostics
        return 1
    fi

    echo ""
    success "Anwendung ist bereit!"
    show_success_message
    open_browser
    return 0
}

########################################
# Show failure diagnostics
########################################
show_failure_diagnostics() {
    echo -e "${YELLOW}========== Fehlerdiagnose ==========${NC}"
    echo ""
    echo "Container-Status:"
    docker compose ps 2>/dev/null
    echo ""
    echo "Letzte Log-Eintraege:"
    docker compose logs --tail=20 2>/dev/null
    echo ""
    echo -e "${YELLOW}========== Moegliche Ursachen ==========${NC}"
    echo ""
    echo "1. Nicht genuegend Arbeitsspeicher"
    echo "   -> Schliessen Sie andere Programme"
    echo "   -> Erhoehen Sie Docker-Speicher in Docker Desktop Einstellungen"
    echo ""
    echo "2. Netzwerkprobleme"
    echo "   -> Ueberpruefen Sie Ihre Internetverbindung"
    echo "   -> Versuchen Sie: docker compose pull"
    echo ""
    echo "3. Docker-Ressourcen"
    echo "   -> Docker Desktop -> Einstellungen -> Resources"
    echo "   -> Empfohlen: Mindestens 8GB RAM, 4 CPUs"
    echo ""
    echo "Naechste Schritte:"
    echo "  1. ./setup.sh logs     # Detaillierte Logs anzeigen"
    echo "  2. ./setup.sh cleanup  # Alles loeschen und neu starten"
    echo ""
}

########################################
# Show success message
########################################
show_success_message() {
    echo ""
    echo -e "${CYAN}=============================================="
    echo "  Installation erfolgreich!"
    echo -e "==============================================${NC}"
    echo ""
    echo "Oeffnen Sie Ihren Browser:"
    echo -e "  ${GREEN}http://localhost:${PORT_FRONTEND}${NC}"

    # Show network URL for access from other machines
    local ip_addr
    if [[ "$OSTYPE" == "darwin"* ]]; then
        ip_addr=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
    else
        ip_addr=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi
    if [ -n "$ip_addr" ]; then
        echo ""
        echo "Zugriff von anderen Geraeten im Netzwerk:"
        echo -e "  ${GREEN}http://${ip_addr}:${PORT_FRONTEND}${NC}"
    fi

    echo ""
    echo "Nuetzliche Befehle:"
    echo "  ./setup.sh stop      Anwendung stoppen"
    echo "  ./setup.sh status    Status anzeigen"
    echo "  ./setup.sh logs      Logs anzeigen"
    echo ""
}

########################################
# Open browser
########################################
open_browser() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        open "http://localhost:${PORT_FRONTEND}" 2>/dev/null || true
    elif command -v xdg-open &> /dev/null; then
        xdg-open "http://localhost:${PORT_FRONTEND}" 2>/dev/null || true
    fi
}

########################################
# Start the application
########################################
do_start() {
    echo ""
    echo -e "${CYAN}=============================================="
    echo "  Protokollierungsassistenz - Setup"
    echo -e "==============================================${NC}"
    echo ""

    # Pre-flight checks
    check_docker || exit 1
    check_existing_installation || exit 1
    check_disk_space || exit 1
    check_ram
    check_ports || exit 1
    check_gpu

    # Create uploads directory
    mkdir -p uploads

    # Start the application
    echo ""
    info "Starte die Anwendung..."

    if [ ${#MISSING_ITEMS[@]} -gt 0 ]; then
        echo "Downloads koennen einige Minuten dauern."
    fi
    echo ""

    pull_images
    echo ""

    if [ "$USE_GPU" = true ]; then
        info "Starte im GPU-Modus..."
        docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
    else
        info "Starte im CPU-Modus..."
        docker compose up -d
    fi

    wait_for_services
}

########################################
# Stop the application
########################################
do_stop() {
    echo ""
    info "Stoppe die Anwendung..."
    docker compose down
    success "Anwendung gestoppt"
    echo ""
}

########################################
# Show status
########################################
do_status() {
    echo ""
    echo -e "${CYAN}=============================================="
    echo "  Protokollierungsassistenz - Status"
    echo -e "==============================================${NC}"
    echo ""

    # Check if containers exist
    if ! docker compose ps -q 2>/dev/null | grep -q .; then
        echo "Die Anwendung ist nicht gestartet."
        echo ""
        echo "Starten mit: ./setup.sh"
        return
    fi

    echo "Container-Status:"
    docker compose ps
    echo ""

    # Check health
    if curl -s "http://localhost:${PORT_BACKEND}/health" > /dev/null 2>&1; then
        echo -e "${GREEN}Backend: Erreichbar${NC}"
    else
        echo -e "${RED}Backend: Nicht erreichbar${NC}"
    fi

    if curl -s "http://localhost:${PORT_FRONTEND}" > /dev/null 2>&1; then
        echo -e "${GREEN}Frontend: Erreichbar${NC}"
    else
        echo -e "${RED}Frontend: Nicht erreichbar${NC}"
    fi

    if curl -s "http://localhost:${PORT_OLLAMA}/api/tags" > /dev/null 2>&1; then
        echo -e "${GREEN}Ollama: Erreichbar${NC}"
    else
        echo -e "${RED}Ollama: Nicht erreichbar${NC}"
    fi

    echo ""
    echo "URL: http://localhost:${PORT_FRONTEND}"
    echo ""
}

########################################
# Restart the application
########################################
do_restart() {
    echo ""
    info "Starte die Anwendung neu..."
    docker compose restart
    success "Anwendung neu gestartet"
    echo ""

    # Brief wait and check
    sleep 3
    do_status
}

########################################
# Show logs
########################################
do_logs() {
    echo ""
    info "Zeige Live-Logs (Strg+C zum Beenden)..."
    echo ""
    docker compose logs -f
}

########################################
# Cleanup everything
########################################
do_cleanup() {
    echo ""
    warn "ACHTUNG: Dies loescht alle Anwendungsdaten!"
    echo ""
    echo "Folgendes wird entfernt:"
    echo "  - Alle Docker-Container"
    echo "  - Alle Volumes (inkl. heruntergeladener Modelle)"
    echo "  - Hochgeladene Dateien bleiben erhalten (uploads/)"
    echo ""
    read -p "Sind Sie sicher? (j/N): " -n 1 -r
    echo ""

    if [[ ! $REPLY =~ ^[Jj]$ ]]; then
        echo "Abgebrochen."
        exit 0
    fi

    info "Raeume auf..."
    docker compose down -v --remove-orphans 2>/dev/null
    success "Aufraeumen abgeschlossen"
    echo ""

    read -p "Moechten Sie die Anwendung jetzt neu installieren? (J/n): " -n 1 -r
    echo ""

    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        do_start
    fi
}

########################################
# Main entry point
########################################
main() {
    local command="${1:-start}"

    case "$command" in
        start|"")
            do_start
            ;;
        stop)
            do_stop
            ;;
        status)
            do_status
            ;;
        restart)
            do_restart
            ;;
        logs)
            do_logs
            ;;
        cleanup)
            do_cleanup
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            error "Unbekannter Befehl: $command"
            show_help
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
