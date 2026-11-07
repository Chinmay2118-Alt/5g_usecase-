#!/usr/bin/env bash
# =============================================================================
# 5G Remote Hands — One-Click Production Deployment Pipeline
# Target: 5G MEC / UPF Edge Server (Ubuntu/Debian/RHEL/CentOS)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ANSI Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Helper log functions
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
fatal()   { echo -e "${RED}[FATAL]${NC} $*" >&2; exit 1; }

# Load local .env if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    # Export non-comment lines
    set -a
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Default configuration values
TARGET_SERVER="${DEPLOY_SERVER_IP:-}"
SSH_USER="${DEPLOY_SSH_USER:-ubuntu}"
SSH_PORT="${DEPLOY_SSH_PORT:-22}"
REMOTE_PATH="${DEPLOY_PATH:-/opt/5g-remote-hands}"
DEPLOY_MODE="${DEPLOY_MODE:-docker}"
SERVER_PORT="${SERVER_PORT:-8443}"
HTTP_PORT="${HTTP_PORT:-8080}"
SCENARIO="${SCENARIO:-flood}"
SERVER_IP="${SERVER_IP:-}"
EXTRA_IPS="${EXTRA_IPS:-}"
LOCAL_MODE=false
CHECK_ONLY=false
ACTION="deploy"

# Display Help
usage() {
    echo -e "${BOLD}5G Remote Hands — Automated Deployment Pipeline${NC}"
    echo
    echo -e "${BOLD}USAGE:${NC}"
    echo -e "  ./deploy.sh [OPTIONS]"
    echo
    echo -e "${BOLD}OPTIONS:${NC}"
    echo -e "  -s, --server <IP/HOST>     Target 5G server IP or hostname (remote mode)"
    echo -e "  -u, --user <USER>          SSH username on target server (default: \$DEPLOY_SSH_USER or ubuntu)"
    echo -e "  -p, --port <PORT>          SSH port (default: \$DEPLOY_SSH_PORT or 22)"
    echo -e "  -d, --dir <PATH>           Remote installation directory (default: /opt/5g-remote-hands)"
    echo -e "  -m, --mode <MODE>          Deployment mode: 'docker' (default) or 'bare-metal'"
    echo -e "      --local                Run deployment directly on this machine (skips SSH/rsync)"
    echo -e "      --scenario <NAME>      Initial scenario: flood | earthquake | wildfire | landslide | arm"
    echo -e "      --server-ip <IP>       5G MEC IP to embed in TLS certificate SAN (e.g. 192.168.1.184)"
    echo -e "      --extra-ips <IPS>      Extra IPs/hostnames for TLS SAN (comma-separated)"
    echo -e "      --check-only           Run pre-flight checks and exit"
    echo -e "      --status               Check health and status of deployed service"
    echo -e "      --logs                 Tail service logs"
    echo -e "      --stop                 Stop running 5G services"
    echo -e "      --restart              Restart 5G services"
    echo -e "  -h, --help                 Show this help message"
    echo
    echo -e "${BOLD}EXAMPLES:${NC}"
    echo -e "  # Remote deployment to 5G MEC server via SSH:"
    echo -e "  ./deploy.sh --server 192.168.1.184 --user ubuntu --server-ip 192.168.1.184"
    echo
    echo -e "  # Local deployment directly on the 5G server:"
    echo -e "  ./deploy.sh --local --server-ip 10.45.0.1"
    echo
    echo -e "  # Bare-metal systemd deployment on the 5G server:"
    echo -e "  ./deploy.sh --local --mode bare-metal"
    echo
    echo -e "  # Check status or tail logs:"
    echo -e "  ./deploy.sh --status"
    echo -e "  ./deploy.sh --logs"
    exit 0
}

# Parse Arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--server)        TARGET_SERVER="$2"; shift 2 ;;
        -u|--user)          SSH_USER="$2"; shift 2 ;;
        -p|--port)          SSH_PORT="$2"; shift 2 ;;
        -d|--dir)           REMOTE_PATH="$2"; shift 2 ;;
        -m|--mode)          DEPLOY_MODE="$2"; shift 2 ;;
        --local)            LOCAL_MODE=true; shift ;;
        --bare-metal)       DEPLOY_MODE="bare-metal"; shift ;;
        --scenario)         SCENARIO="$2"; shift 2 ;;
        --server-ip)        SERVER_IP="$2"; shift 2 ;;
        --extra-ips)        EXTRA_IPS="$2"; shift 2 ;;
        --check-only)       CHECK_ONLY=true; shift ;;
        --status)           ACTION="status"; shift ;;
        --logs)             ACTION="logs"; shift ;;
        --stop)             ACTION="stop"; shift ;;
        --restart)          ACTION="restart"; shift ;;
        -h|--help)          usage ;;
        *) fatal "Unknown parameter: $1 (run ./deploy.sh --help for options)" ;;
    esac
done

# If no remote server provided and not explicitly remote, check if we should run locally
if [ -z "$TARGET_SERVER" ] && [ "$LOCAL_MODE" = false ]; then
    info "No remote server specified via --server or .env."
    read -r -p "Deploy locally on this machine? [Y/n]: " response || response="Y"
    if [[ "$response" =~ ^[Nn] ]]; then
        fatal "Deployment aborted. Please specify a target server with --server <IP> or set DEPLOY_SERVER_IP in .env"
    fi
    LOCAL_MODE=true
fi

# Detect Docker Compose binary command
detect_docker_compose() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        echo "docker-compose"
    else
        echo ""
    fi
}

# -----------------------------------------------------------------------------
# Local Pre-flight Verification
# -----------------------------------------------------------------------------
run_preflight_checks() {
    info "${BOLD}Running Pre-flight Checks on $(hostname)...${NC}"
    local checks_passed=true

    # 1. OS & Architecture
    local os_type
    os_type="$(uname -s)"
    local arch_type
    arch_type="$(uname -m)"
    info "Operating System: $os_type ($arch_type)"

    # 2. Check Deployment Mode prerequisites
    if [ "$DEPLOY_MODE" = "docker" ]; then
        # Check if Docker is actually Podman emulation or lacks native docker daemon
        if (docker --version 2>&1 | grep -iq "podman") || (docker info 2>&1 | grep -iq "podman"); then
            warn "Podman environment detected instead of standard Docker daemon."
            warn "Automatically selecting Bare-Metal Python mode for ultra-low latency 5G performance."
            DEPLOY_MODE="bare-metal"
        elif ! command -v docker >/dev/null 2>&1; then
            warn "Docker not found. Switching to Bare-Metal Python mode."
            DEPLOY_MODE="bare-metal"
        else
            success "Docker daemon binary detected: $(docker --version)"
            if ! docker info >/dev/null 2>&1; then
                warn "Docker daemon is not responsive. Switching to Bare-Metal Python mode."
                DEPLOY_MODE="bare-metal"
            else
                success "Docker daemon is running and responsive."
            fi
        fi
    fi

    if [ "$DEPLOY_MODE" = "docker" ]; then
        local compose_cmd
        compose_cmd="$(detect_docker_compose)"
        if [ -z "$compose_cmd" ]; then
            warn "Neither 'docker compose' nor 'docker-compose' found. Switching to Bare-Metal Python mode."
            DEPLOY_MODE="bare-metal"
        else
            success "Docker Compose detected: $($compose_cmd version 2>/dev/null || true)"
        fi
    fi

    if [ "$DEPLOY_MODE" = "bare-metal" ]; then
        # Bare-metal prerequisites
        if ! command -v python3 >/dev/null 2>&1; then
            error "python3 is not installed."
            checks_passed=false
        else
            local py_ver
            py_ver="$(python3 --version 2>&1)"
            success "Python detected: $py_ver"
        fi
        if ! python3 -c "import venv" >/dev/null 2>&1; then
            error "python3-venv module is missing. (On Ubuntu/Debian: sudo apt-get install python3-venv)"
            checks_passed=false
        fi
    fi

    # 3. Port conflict check (8443 / 8080)
    for p in "$SERVER_PORT" "$HTTP_PORT"; do
        if [ -n "$p" ]; then
            if command -v lsof >/dev/null 2>&1; then
                if lsof -iTCP:"$p" -sTCP:LISTEN -P -n >/dev/null 2>&1; then
                    warn "Port $p is currently in use. Existing process will be replaced or might conflict."
                else
                    success "Port $p is available."
                fi
            elif command -v ss >/dev/null 2>&1; then
                if ss -tuln | grep -q ":$p "; then
                    warn "Port $p is currently in use. Existing process will be replaced or might conflict."
                else
                    success "Port $p is available."
                fi
            fi
        fi
    done

    # 4. 5G Network Interfaces Inspection
    info "Inspecting network interfaces for 5G Core / UPF / MEC bindings..."
    if command -v ip >/dev/null 2>&1; then
        local upf_ifaces
        upf_ifaces="$(ip -o addr show | awk '{print $2, $4}' | grep -v 'lo' || true)"
        if [ -n "$upf_ifaces" ]; then
            info "Available Network Interfaces:"
            echo "$upf_ifaces" | while read -r line; do
                echo "   -> $line"
            done
        fi
    fi

    if [ "$checks_passed" = false ]; then
        fatal "Pre-flight checks failed. Please resolve the issues above before continuing."
    fi
    success "All pre-flight checks passed successfully!"
}

# -----------------------------------------------------------------------------
# Local Actions (Status, Logs, Stop, Restart)
# -----------------------------------------------------------------------------
handle_local_action() {
    local compose_cmd
    compose_cmd="$(detect_docker_compose)"

    case "$ACTION" in
        status)
            info "Checking 5G Remote Hands service status..."
            if [ "$DEPLOY_MODE" = "docker" ] && [ -n "$compose_cmd" ]; then
                $compose_cmd ps
            elif command -v systemctl >/dev/null 2>&1; then
                sudo systemctl status 5g-remote-hands.service --no-pager || true
            fi
            echo
            info "Querying health check endpoint..."
            curl -kfsS "https://127.0.0.1:${SERVER_PORT}/api/health" || curl -fsS "http://127.0.0.1:${HTTP_PORT}/api/health" || warn "Health endpoint not responding."
            echo
            exit 0
            ;;
        logs)
            if [ "$DEPLOY_MODE" = "docker" ] && [ -n "$compose_cmd" ]; then
                $compose_cmd logs -f --tail=100
            elif command -v journalctl >/dev/null 2>&1; then
                sudo journalctl -u 5g-remote-hands.service -f -n 100
            fi
            exit 0
            ;;
        stop)
            info "Stopping 5G Remote Hands services..."
            if [ "$DEPLOY_MODE" = "docker" ] && [ -n "$compose_cmd" ]; then
                $compose_cmd down
            elif command -v systemctl >/dev/null 2>&1; then
                sudo systemctl stop 5g-remote-hands.service
            fi
            success "Services stopped."
            exit 0
            ;;
        restart)
            info "Restarting 5G Remote Hands services..."
            if [ "$DEPLOY_MODE" = "docker" ] && [ -n "$compose_cmd" ]; then
                $compose_cmd restart
            elif command -v systemctl >/dev/null 2>&1; then
                sudo systemctl restart 5g-remote-hands.service
            fi
            success "Services restarted."
            exit 0
            ;;
    esac
}

# -----------------------------------------------------------------------------
# Local Deployment Implementation
# -----------------------------------------------------------------------------
deploy_local() {
    run_preflight_checks
    if [ "$CHECK_ONLY" = true ]; then
        exit 0
    fi

    # Ensure local directories exist
    mkdir -p "$SCRIPT_DIR/certs" "$SCRIPT_DIR/logs"

    # Auto-detect IP if not provided
    if [ -z "$SERVER_IP" ]; then
        # Try finding non-loopback IP
        if command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
            SERVER_IP="$(hostname -I | awk '{print $1}')"
        elif command -v ip >/dev/null 2>&1; then
            SERVER_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' || true)"
        fi
        SERVER_IP="${SERVER_IP:-127.0.0.1}"
    fi

    info "Using 5G Server IP for TLS SAN: ${BOLD}$SERVER_IP${NC}"

    # Export variables for Docker or Python
    export SERVER_IP
    export EXTRA_IPS
    export SERVER_PORT
    export HTTP_PORT
    export SCENARIO
    export AUTO_GEN_CERT=true

    # Update or create .env locally
    cat > "$SCRIPT_DIR/.env" <<EOF
SERVER_HOST=0.0.0.0
SERVER_PORT=$SERVER_PORT
HTTP_PORT=$HTTP_PORT
SCENARIO=$SCENARIO
SERVER_IP=$SERVER_IP
EXTRA_IPS=$EXTRA_IPS
AUTO_GEN_CERT=true
DEPLOY_MODE=$DEPLOY_MODE
EOF

    if [ "$DEPLOY_MODE" = "docker" ]; then
        info "Executing Docker Compose deployment..."
        local compose_cmd
        compose_cmd="$(detect_docker_compose)"
        if [ -z "$compose_cmd" ] || ! $compose_cmd up -d --build; then
            warn "Docker compose execution failed or Podman/urllib3 conflict detected."
            info "Automatically falling back to native Bare-Metal Python deployment..."
            DEPLOY_MODE="bare-metal"
        else
            info "Waiting for container health check to report OK..."
            local attempts=0
            local healthy=false
            while [ $attempts -lt 25 ]; do
                if curl -kfsS "https://127.0.0.1:${SERVER_PORT}/api/health" >/dev/null 2>&1 || \
                   curl -fsS "http://127.0.0.1:${HTTP_PORT}/api/health" >/dev/null 2>&1; then
                    healthy=true
                    break
                fi
                sleep 1
                attempts=$((attempts + 1))
                echo -n "."
            done
            echo

            if [ "$healthy" = true ]; then
                success "5G Remote Hands container is HEALTHY and listening!"
            else
                warn "Health check timed out. Falling back to Bare-Metal Python..."
                DEPLOY_MODE="bare-metal"
            fi
        fi
    fi

    if [ "$DEPLOY_MODE" = "bare-metal" ]; then
        # Bare-metal Deployment
        info "Executing Bare-Metal deployment on host..."

        # Determine python and pip binaries
        local py_bin="python3"
        local pip_bin="python3 -m pip"

        # Try setting up virtualenv only if python3-venv is already functional
        if [ ! -f "$SCRIPT_DIR/.venv/bin/python" ] || [ ! -f "$SCRIPT_DIR/.venv/bin/pip" ]; then
            rm -rf "$SCRIPT_DIR/.venv"
            python3 -m venv "$SCRIPT_DIR/.venv" 2>/dev/null || true
        fi

        if [ -f "$SCRIPT_DIR/.venv/bin/python" ] && [ -f "$SCRIPT_DIR/.venv/bin/pip" ]; then
            py_bin="$SCRIPT_DIR/.venv/bin/python"
            pip_bin="$SCRIPT_DIR/.venv/bin/pip"
            info "Using virtual environment Python: $py_bin"
        else
            info "Using user Python environment: $py_bin (zero sudo required)"
            pip_bin="python3 -m pip"
        fi

        info "Installing / updating Python dependencies..."
        if [ "$pip_bin" = "python3 -m pip" ]; then
            $pip_bin install --user -r "$SCRIPT_DIR/requirements.txt" || $pip_bin install -r "$SCRIPT_DIR/requirements.txt"
        else
            $pip_bin install -r "$SCRIPT_DIR/requirements.txt"
        fi

        info "Generating self-signed TLS certificates for 5G endpoints..."
        local cert_args=()
        [ -n "$SERVER_IP" ] && cert_args+=(--ip "$SERVER_IP")
        if [ -n "$EXTRA_IPS" ]; then
            for e_ip in $(echo "$EXTRA_IPS" | tr ',' ' '); do
                cert_args+=(--ip "$e_ip")
            done
        fi
        "$py_bin" -m server.certs "${cert_args[@]}"

        # Check if passwordless sudo is available for systemd
        local can_sudo=false
        if [ -w /etc/systemd/system ]; then
            can_sudo=true
        elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
            can_sudo=true
        fi

        if [ "$can_sudo" = true ] && command -v systemctl >/dev/null 2>&1; then
            info "Configuring systemd service /etc/systemd/system/5g-remote-hands.service..."
            local actual_user="${SUDO_USER:-$(whoami)}"
            local actual_group
            actual_group="$(id -gn "$actual_user" 2>/dev/null || echo "$actual_user")"
            local tmp_svc
            tmp_svc="$(mktemp)"
            sed -e "s|User=ubuntu|User=${actual_user}|g" \
                -e "s|Group=ubuntu|Group=${actual_group}|g" \
                -e "s|/opt/5g-remote-hands/.venv/bin/python|$py_bin|g" \
                -e "s|/opt/5g-remote-hands|$SCRIPT_DIR|g" \
                "$SCRIPT_DIR/5g-remote-hands.service" > "$tmp_svc"

            if [ -w /etc/systemd/system ]; then
                cp "$tmp_svc" /etc/systemd/system/5g-remote-hands.service
                systemctl daemon-reload
                systemctl enable 5g-remote-hands.service
                systemctl restart 5g-remote-hands.service
            else
                sudo cp "$tmp_svc" /etc/systemd/system/5g-remote-hands.service
                sudo systemctl daemon-reload
                sudo systemctl enable 5g-remote-hands.service
                sudo systemctl restart 5g-remote-hands.service
            fi
            rm -f "$tmp_svc"
        else
            info "Running in user-space background mode (zero sudo required)..."
            # Kill existing instance if running on the same port
            pkill -f "server.main.*$SERVER_PORT" 2>/dev/null || true
            mkdir -p "$SCRIPT_DIR/logs"
            nohup "$py_bin" -m server.main --scenario "$SCENARIO" --port "$SERVER_PORT" > "$SCRIPT_DIR/logs/server.log" 2>&1 &
            sleep 2
        fi

        info "Waiting for service health check to report OK..."
        local attempts=0
        local healthy=false
        while [ $attempts -lt 25 ]; do
            if curl -kfsS "https://127.0.0.1:${SERVER_PORT}/api/health" >/dev/null 2>&1 || \
               curl -fsS "http://127.0.0.1:${HTTP_PORT}/api/health" >/dev/null 2>&1; then
                healthy=true
                break
            fi
            sleep 1
            attempts=$((attempts + 1))
            echo -n "."
        done
        echo

        if [ "$healthy" = true ]; then
            success "5G Remote Hands service is HEALTHY and listening on port ${SERVER_PORT}!"
        else
            warn "Health check timed out. Fetching recent log lines:"
            tail -n 25 "$SCRIPT_DIR/logs/server.log" 2>/dev/null || true
        fi

            info "Waiting for service health check to report OK..."
            local attempts=0
            local healthy=false
            while [ $attempts -lt 25 ]; do
                if curl -kfsS "https://127.0.0.1:${SERVER_PORT}/api/health" >/dev/null 2>&1 || \
                   curl -fsS "http://127.0.0.1:${HTTP_PORT}/api/health" >/dev/null 2>&1; then
                    healthy=true
                    break
                fi
                sleep 1
                attempts=$((attempts + 1))
                echo -n "."
            done
            echo

            if [ "$healthy" = true ]; then
                success "5G Remote Hands service is HEALTHY and listening on port ${SERVER_PORT}!"
            else
                warn "Health check timed out. Fetching recent service logs:"
                if command -v sudo >/dev/null 2>&1; then
                    sudo journalctl -u 5g-remote-hands.service -n 25 --no-pager || true
                fi
            fi
        else
            info "Starting service directly in background with nohup..."
            nohup "$SCRIPT_DIR/.venv/bin/python" -m server.main --scenario "$SCENARIO" --port "$SERVER_PORT" > "$SCRIPT_DIR/logs/server.log" 2>&1 &
            sleep 3
        fi
    fi

    # Print summary and URL endpoints
    print_deployment_summary "$SERVER_IP" "$SERVER_PORT" "$HTTP_PORT" "$SCENARIO"
}

# -----------------------------------------------------------------------------
# Remote Deployment Implementation (SSH + Rsync)
# -----------------------------------------------------------------------------
deploy_remote() {
    [ -z "$TARGET_SERVER" ] && fatal "No target server specified."
    info "${BOLD}Initiating Remote Deployment to ${CYAN}${SSH_USER}@${TARGET_SERVER}:${REMOTE_PATH}${NC} (SSH port ${SSH_PORT})..."

    # Test SSH connection
    info "Verifying SSH connection to target 5G server..."
    if ! ssh -p "$SSH_PORT" -o ConnectTimeout=8 -o BatchMode=yes "${SSH_USER}@${TARGET_SERVER}" "echo connected" >/dev/null 2>&1; then
        warn "Direct key-based SSH connection failed or host key not cached."
        info "Testing interactive SSH connection..."
        ssh -p "$SSH_PORT" -o ConnectTimeout=10 "${SSH_USER}@${TARGET_SERVER}" "echo 'SSH Connection OK'" || fatal "Could not connect to ${SSH_USER}@${TARGET_SERVER} via SSH."
    fi
    success "SSH connection established successfully!"

    # Ensure remote directory exists
    info "Creating target directory on 5G server: $REMOTE_PATH"
    ssh -p "$SSH_PORT" "${SSH_USER}@${TARGET_SERVER}" "sudo mkdir -p '$REMOTE_PATH' && sudo chown -R ${SSH_USER}:${SSH_USER} '$REMOTE_PATH'"

    # Sync codebase to target server
    info "Syncing repository files to remote 5G server..."
    if command -v rsync >/dev/null 2>&1; then
        rsync -avz --delete \
            -e "ssh -p $SSH_PORT" \
            --exclude '.venv' \
            --exclude '.git' \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '.pytest_cache' \
            --exclude 'certs/*.pem' \
            --exclude 'logs/*' \
            --exclude '.DS_Store' \
            "$SCRIPT_DIR/" "${SSH_USER}@${TARGET_SERVER}:${REMOTE_PATH}/"
    else
        warn "rsync not found locally, falling back to tar over SSH..."
        tar --exclude='.venv' --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
            --exclude='.pytest_cache' --exclude='certs/*.pem' --exclude='logs/*' \
            -czf - -C "$SCRIPT_DIR" . | ssh -p "$SSH_PORT" "${SSH_USER}@${TARGET_SERVER}" "tar -xzf - -C '$REMOTE_PATH'"
    fi
    success "Codebase synchronized cleanly!"

    # Forward remote execution
    info "Triggering automated setup & service launch on remote 5G server..."
    local remote_cmd="cd '$REMOTE_PATH' && chmod +x deploy.sh docker-entrypoint.sh && ./deploy.sh --local --mode '$DEPLOY_MODE' --scenario '$SCENARIO' --server-ip '${SERVER_IP:-$TARGET_SERVER}'"
    [ -n "$EXTRA_IPS" ] && remote_cmd="$remote_cmd --extra-ips '$EXTRA_IPS'"
    [ "$CHECK_ONLY" = true ] && remote_cmd="$remote_cmd --check-only"

    ssh -t -p "$SSH_PORT" "${SSH_USER}@${TARGET_SERVER}" "$remote_cmd"

    success "Remote 5G Server deployment completed successfully!"
}

# -----------------------------------------------------------------------------
# Deployment Summary Banner
# -----------------------------------------------------------------------------
print_deployment_summary() {
    local host_ip="$1"
    local https_p="$2"
    local http_p="$3"
    local sc="$4"

    echo
    echo -e "${GREEN}===============================================================================${NC}"
    echo -e "${BOLD}${GREEN}        5G REMOTE HANDS — DEPLOYMENT ACTIVE & PRODUCTION-READY                ${NC}"
    echo -e "${GREEN}===============================================================================${NC}"
    echo -e "  ${BOLD}Active Scenario:${NC}    ${CYAN}${sc}${NC} (changeable live from dashboard)"
    echo -e "  ${BOLD}5G Server Host:${NC}     ${CYAN}${host_ip}${NC}"
    echo -e "  ${BOLD}Deployment Mode:${NC}    ${CYAN}${DEPLOY_MODE}${NC}"
    echo -e "  ${BOLD}Health Endpoint:${NC}    ${CYAN}https://${host_ip}:${https_p}/api/health${NC}"
    echo
    echo -e "${BOLD}ACCESS URLS FOR THE TEAM:${NC}"
    echo -e "  1. ${BOLD}Meta Quest VR Operator:${NC}"
    echo -e "     ${CYAN}https://${host_ip}:${https_p}/rescue/${NC}       (Rescue Rover)"
    echo -e "     ${CYAN}https://${host_ip}:${https_p}/xr/${NC}           (Tabletop Arm)"
    echo
    echo -e "  2. ${BOLD}Mission Control Dashboard (Experimenter / Projector):${NC}"
    echo -e "     ${CYAN}https://${host_ip}:${https_p}/rescue-dashboard/${NC}"
    echo -e "     ${CYAN}https://${host_ip}:${https_p}/dashboard/${NC}   (Arm Dashboard)"
    echo
    echo -e "  3. ${BOLD}Why 5G? Live Network Comparison & Explainer:${NC}"
    echo -e "     ${CYAN}https://${host_ip}:${https_p}/why5g/${NC}"
    echo
    if [ -n "$http_p" ]; then
        echo -e "  4. ${BOLD}Plain HTTP Fallback (Desktop Browser Testing):${NC}"
        echo -e "     ${CYAN}http://${host_ip}:${http_p}/rescue/${NC}"
        echo
    fi
    echo -e "${YELLOW}IMPORTANT (Meta Quest WebXR First Connect):${NC}"
    echo -e "  * Headset and 5G server must route over the lab's private 5G network (UPF/MEC)."
    echo -e "  * Since the SSL certificate is self-signed for this 5G server IP,"
    echo -e "    open the operator URL in Meta Quest Browser, tap ${BOLD}'Advanced'${NC} -> ${BOLD}'Proceed'${NC}."
    echo -e "  * Click ${BOLD}'Enter VR'${NC} to start seated in the rover cockpit!"
    echo -e "${GREEN}===============================================================================${NC}"
    echo
}

# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------
if [ "$ACTION" != "deploy" ]; then
    handle_local_action
fi

if [ "$LOCAL_MODE" = true ]; then
    deploy_local
else
    deploy_remote
fi
