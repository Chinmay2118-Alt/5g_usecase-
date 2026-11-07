# 5G Remote Hands — Production Deployment Guide & Runbook

This guide provides end-to-end instructions for deploying the **5G Remote Hands** VR teleoperation and edge simulation platform onto a target **5G Multi-Access Edge Computing (MEC) / Core (UPF) Server**.

---

## 1. Architecture & 5G Network Topology

The system operates as an ultra-low latency teleoperation service situated on the MEC host directly attached to the 5G Core User Plane Function (UPF) / Data Network (N6 interface).

```
 ┌──────────────────────────────────────┐
 │   Meta Quest 3 / Pro Headset         │
 │   - WebXR Browser (Cockpit & Table)  │
 │   - 50 Hz Commands (Uplink)          │
 │   - 30 Hz State + Deltas (Downlink)  │
 └──────────────────┬───────────────────┘
                    │ Wi-Fi 6 / NR-U
 ┌──────────────────▼───────────────────┐
 │   5G AP / CPE (Customer Premise)     │
 └──────────────────┬───────────────────┘
                    │ 3GPP Sub-6 GHz / mmWave (GTP-U Tunnel / N3)
 ┌──────────────────▼───────────────────┐
 │   gNodeB (CU / DU)                   │
 └──────────────────┬───────────────────┘
                    │ GTP-U (UDP 2152) / N3 Interface
 ┌──────────────────▼───────────────────┐
 │   5G Core / UPF (User Plane Function)│
 └──────────────────┬───────────────────┘
                    │ High-Speed Data Network (N6 Interface)
 ┌──────────────────▼─────────────────────────────────────────────────┐
 │   Target 5G MEC Host Server (Ubuntu / Linux)                       │
 │                                                                    │
 │   ┌────────────────────────────────────────────────────────────┐   │
 │   │  5G Remote Hands Container / Service (:8443 HTTPS/WSS)     │   │
 │   │  ├─ 100 Hz Safety Engine & Watchdog (180ms deadline)       │   │
 │   │  ├─ Multi-Disaster Sim (Flood, Earthquake, Fire, Slide)    │   │
 │   │  ├─ Edge-AI Ground Truth Detector & Stop Guard             │   │
 │   │  ├─ WebSocket Delay Line & Network Comparison Engine       │   │
 │   │  └─ Vendored WebXR Frontend & Live Dashboards              │   │
 │   └────────────────────────────────────────────────────────────┘   │
 └────────────────────────────────────────────────────────────────────┘
```

### Network Endpoints & Ports

| Port | Protocol | Purpose | Access Required By |
| :--- | :--- | :--- | :--- |
| **8443** | TCP (HTTPS/WSS) | **Primary Production Port**: WebXR operator client, experimenter dashboard, Why 5G comparison, and WebSocket streams. | Meta Quest Browser, Experimenter Laptop/Projector |
| **8080** | TCP (HTTP/WS) | Optional fallback port for plain HTTP desktop testing. | Local development / debugging |

---

## 2. Target Server Requirements

- **Operating System**: Ubuntu 20.04 / 22.04 / 24.04 LTS, Debian 11/12, or RHEL/CentOS 8/9.
- **Hardware**: Minimum 2 vCPU, 2 GB RAM, 5 GB free disk space.
- **Network Interface**: Static IP assigned on the 5G LAN / UPF subnet (e.g. `192.168.1.184` or `10.45.0.1`).
- **Software**:
  - **Docker Mode (Recommended)**: Docker Engine 24+ and Docker Compose v2 (`docker compose`).
  - **Bare-Metal Mode (Fallback)**: Python 3.9+ with `python3-venv` and `systemd`.

---

## 3. Configuration (`.env`)

Copy `.env.example` to `.env` on your local machine or target server and configure:

```bash
cp .env.example .env
```

Key configuration parameters:

```ini
# Interface binding (0.0.0.0 binds to all 5G UPF / MEC network interfaces)
SERVER_HOST=0.0.0.0
SERVER_PORT=8443

# Initial simulation scenario: flood | earthquake | wildfire | landslide | arm
SCENARIO=flood

# IP address of the 5G MEC server (used in self-signed TLS cert SAN)
SERVER_IP=192.168.1.184
EXTRA_IPS=127.0.0.1,mec.5g-lab.local

# Automated deployment settings (for deploy.sh)
DEPLOY_SERVER_IP=192.168.1.184
DEPLOY_SSH_USER=ubuntu
DEPLOY_SSH_PORT=22
DEPLOY_PATH=/opt/5g-remote-hands
DEPLOY_MODE=docker
```

---

## 4. Deployment Methods

### Method A: One-Click Automated Remote Deployment (From Local Machine)

Run `deploy.sh` directly from your local terminal. The script performs pre-flight checks, syncs files via `rsync`, builds the containers, and monitors health:

```bash
# Basic deployment:
./deploy.sh --server <5G_SERVER_IP> --user ubuntu --server-ip <5G_SERVER_IP>

# Example with custom SSH port and initial scenario:
./deploy.sh --server 192.168.1.184 --user ubuntu --port 22 --server-ip 192.168.1.184 --scenario earthquake

# Bare-metal fallback deployment over SSH:
./deploy.sh --server 192.168.1.184 --user ubuntu --bare-metal
```

---

### Method B: Direct Docker Compose Deployment (On the 5G Server)

If you have already logged into the 5G server via SSH:

```bash
# 1. Clone repository to /opt/5g-remote-hands
sudo mkdir -p /opt/5g-remote-hands
sudo chown -R $USER:$USER /opt/5g-remote-hands
git clone https://github.com/Chinmay2118-Alt/5g_usecase- /opt/5g-remote-hands
cd /opt/5g-remote-hands

# 2. Configure environment
cp .env.example .env
# Edit .env with your server's IP address:
nano .env

# 3. Launch via deploy.sh in local mode:
./deploy.sh --local

# OR directly with docker compose:
docker compose up -d --build

# 4. Verify status and logs:
docker compose ps
docker compose logs -f
```

---

### Method C: Bare-Metal Systemd Deployment (Without Docker)

For environments requiring native execution directly on the host OS:

```bash
cd /opt/5g-remote-hands

# 1. Run local bare-metal installer:
./deploy.sh --local --mode bare-metal

# This sets up the virtualenv, generates TLS certs, and configures the systemd service:
sudo systemctl status 5g-remote-hands.service
```

Manual service control:
```bash
sudo systemctl start 5g-remote-hands.service
sudo systemctl stop 5g-remote-hands.service
sudo systemctl restart 5g-remote-hands.service
sudo journalctl -u 5g-remote-hands.service -f -n 50
```

---

## 5. Meta Quest VR Headset Connection Guide

WebXR requires a secure HTTPS context. Follow these steps to connect the headset:

1. **Connect Wi-Fi**:
   Connect the Meta Quest headset to the Wi-Fi network served by the 5G AP / CPE router.

2. **Open Browser**:
   Open the **Meta Quest Browser** on the headset.

3. **Navigate to Operator URL**:
   Type:
   ```
   https://<SERVER_IP>:8443/rescue/
   ```
   *(Replace `<SERVER_IP>` with your 5G MEC server's actual IP, e.g. `https://192.168.1.184:8443/rescue/`)*

4. **Accept Self-Signed Certificate**:
   The browser will show a standard certificate warning:
   - Select **Advanced**
   - Click **Proceed to `<SERVER_IP>` (unsafe)**

5. **Enter VR**:
   - Tap the **Enter VR** button at the bottom of the screen.
   - You will start seated inside the rover cockpit looking through the front-mounted stereo camera.

### Quest Controller Mapping

| Action | Quest Controller | Desktop Keyboard Fallback |
| :--- | :--- | :--- |
| **Deadman Clutch (Required for motion)** | Hold **Grip** (either hand) | Hold **Space** |
| **Drive / Throttle** | Left thumbstick (Up/Down) | **W** / **S** |
| **Steer** | Right thumbstick (Left/Right) | **A** / **D** |
| **Send Rover (Autonomous Waypoint)** | Point right laser at map & pull **Trigger** | Left-click map table |
| **Switch View (Cockpit $\leftrightarrow$ Miniature Table)** | Press **X** | Press **V** |
| **Arm Rover** | Press **A** (with clutch released) | Press **R** |
| **Cancel Route / Emergency Disarm** | Press **B** | Press **T** / **C** |
| **Emergency Stop (AI / Operator)** | Press **Y** | Press **X** |
| **Recenter Cockpit Seating** | Click **Left Stick** | Recenter view |

---

## 6. Access Endpoints Summary

| User / Role | URL | Purpose |
| :--- | :--- | :--- |
| **VR Operator (Meta Quest)** | `https://<SERVER_IP>:8443/rescue/` | Rescue Rover teleoperation (Cockpit + Map) |
| **VR Operator (Arm Scenario)** | `https://<SERVER_IP>:8443/xr/` | Tabletop IK robot arm pick-and-place |
| **Experimenter / Projector** | `https://<SERVER_IP>:8443/rescue-dashboard/` | Disaster controls, network latency profiles, AI-stop triggers |
| **Arm Dashboard** | `https://<SERVER_IP>:8443/dashboard/` | Arm scenario controls & metrics |
| **Public / Explainer** | `https://<SERVER_IP>:8443/why5g/` | "Why 5G?" live latency & stopping distance comparison |
| **Health API** | `https://<SERVER_IP>:8443/api/health` | Service health status JSON |
| **Metrics / Results API** | `https://<SERVER_IP>:8443/api/results` | Historical test runs, stopping distances, and reaction times |

---

## 7. 5G Low-Latency Host OS Tuning

To achieve guaranteed sub-millisecond network jitter and eliminate packet stalls on Linux 5G MEC edge nodes:

### 1. Docker Host Networking (Recommended for 5G MEC)
In `docker-compose.yml`, switch from bridge port forwarding to host networking to eliminate Docker iptables NAT overhead:
```yaml
services:
  remote-hands:
    network_mode: host
```

### 2. Linux Kernel Socket & TCP Tuning
Apply high-frequency TCP streaming optimizations by adding to `/etc/sysctl.d/99-5g-latency.conf`:
```ini
# Disable slow start on idling connections (keeps 50Hz/100Hz packets snappy)
net.ipv4.tcp_slow_start_after_idle = 0

# Set socket buffer defaults
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216

# Enable low-latency TCP nodelay handling
net.ipv4.tcp_low_latency = 1

# Increase socket queue backlog for bursty streams
net.core.netdev_max_backlog = 10000
net.core.somaxconn = 4096
```
Apply with:
```bash
sudo sysctl --system
```

### 3. CPU Frequency Governor
Ensure CPU frequency does not throttle down during 100 Hz simulation cycles:
```bash
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```

---

## 8. Verification & Diagnostics

### Automated Headless Link-Loss & Watchdog Test
Verify the real-time safety layer and WebSocket responsiveness directly from the server CLI without needing a Quest headset:

```bash
# Run 10 headless trials against the local server:
python3 tools/fake_operator.py --url https://localhost:8443 --trials 10

# Expected output:
# trial  1: PASS  robot held 182.4 ms after last command
# ...
# 10/10 trials held within 200 ms  (hold latency mean 182.1 ms, max 182.9 ms)
# command RTT p50 0.8 ms, p95 1.2 ms over 960 acks
```

### Healthcheck Verification
```bash
curl -kfsS https://localhost:8443/api/health
```
Response:
```json
{
  "status": "ok",
  "scenario": "flood",
  "uptime_s": 124.5,
  "operator_connected": false,
  "dashboards_connected": 1,
  "safety_state": "DISARMED"
}
```

---

## 9. Troubleshooting

### Q1: Quest Browser shows "Your connection is not private" and blocks WebXR
- **Cause**: The self-signed certificate SAN does not match the IP address being accessed in the browser.
- **Fix**: Re-run the certificate generator with your server's exact IP:
  ```bash
  python3 -m server.certs --ip <YOUR_5G_SERVER_IP>
  # In Docker:
  docker compose restart
  ```
  Ensure you tap **Advanced $\rightarrow$ Proceed** in the Quest browser.

### Q2: Robot immediately trips into `HOLDING` state with reason `watchdog`
- **Cause**: Watchdog trips if no valid command arrives within 180 ms.
- **Diagnostics**:
  1. Check if the active network profile has high loss or delay: open the Dashboard (`https://<SERVER_IP>:8443/rescue-dashboard/`) and click the **"Private 5G + MEC"** button.
  2. Inspect command RTT on the dashboard RTT chart. Over the private 5G link, RTT should remain under 15 ms.

### Q3: Port 8443 already in use error
- **Cause**: An existing instance of the server or another web service is occupying port 8443.
- **Fix**: Identify and stop the occupying process:
  ```bash
  sudo lsof -iTCP:8443 -sTCP:LISTEN
  # Or change SERVER_PORT in .env to another port (e.g. 9443).
  ```

### Q4: "Another operator has control" message in Quest browser
- **Cause**: The server maintains a strict 1-operator safety policy to prevent conflicting teleoperation commands.
- **Fix**: If a desktop tab is open to `/rescue/`, close it or press **A** on the Quest controller to claim control back.

