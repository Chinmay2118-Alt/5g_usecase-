# 5G Remote Hands — VR Teleoperation Over Private 5G (Simulation)

An operator wearing a Meta Quest controls a simulated robot over the lab's private 5G network. The server plays two roles:
- **The robot**: simulation, autonomous path planning, and the safety layer.
- **The edge / MEC relay**: WebSockets, latency injection, AI inference guard, and telemetry logging.

The Quest draws the 3D scene itself via WebXR. Only commands (50 Hz) and robot state (30 Hz, ~1–2 KB, plus a ~5 KB fire map delta twice a second in the wildfire) cross the 5G link.

```
Quest browser (WebXR) ──Wi-Fi──► 5G AP/CPE ──► private 5G (gNB · CU-DU · Core/UPF) ──► MEC Server :8443
   commands 50 Hz  ───────────────────────────────────────────────────────────────────►  safety + sim (100 Hz)
   ◄─────────────────────────────────────────────────────────────────────  acks + state 30 Hz
```

---

## 1. Production 5G Server Deployment & Automation

The repository includes a complete automated deployment pipeline for target **Ubuntu / Linux 5G MEC & UPF servers**.

### Option A: One-Click Automated Remote Deployment (From Local Terminal)
Deploy directly from your development machine to the 5G server over SSH:

```bash
# Automated SSH deployment (syncs code, runs pre-flight checks, builds & starts containers)
./deploy.sh --server <5G_SERVER_IP> --user ubuntu --server-ip <5G_SERVER_IP>
```
*(Example: `./deploy.sh --server 192.168.1.184 --user ubuntu --server-ip 192.168.1.184`)*

### Option B: Docker Compose Deployment (On the 5G Server)
SSH into the 5G server and run:

```bash
git clone https://github.com/Chinmay2118-Alt/5g_usecase- /opt/5g-remote-hands
cd /opt/5g-remote-hands

# Configure server IP and ports
cp .env.example .env
nano .env

# Deploy with one command (or run ./deploy.sh --local)
docker compose up -d --build

# Verify container health and logs
docker compose ps
docker compose logs -f --tail=30
```

### Option C: Bare-Metal Systemd Deployment (Without Docker)
For deployments directly on host Linux without container overhead:

```bash
cd /opt/5g-remote-hands
./deploy.sh --local --mode bare-metal
```
This automatically sets up the Python virtualenv, generates TLS certs, and enables the `5g-remote-hands.service` systemd unit file with automatic restart on crash (`Restart=always`).

### Service Control Commands
```bash
./deploy.sh --status    # Query container status and /api/health endpoint
./deploy.sh --logs      # Tail real-time service logs
./deploy.sh --restart   # Restart running services
./deploy.sh --stop      # Gracefully stop services
```

---

## 2. Meta Quest VR Headset Setup & First Connect

WebXR runs in the native **Meta Quest Browser** with zero app installation needed.

1. **Connect Wi-Fi:** Connect the Meta Quest headset to the Wi-Fi network served by the 5G AP / CPE router.
2. **Open Browser:** Launch the native **Meta Quest Browser** on the headset.
3. **Navigate to Operator URL:** Enter `https://<5G_SERVER_IP>:8443/rescue/`
4. **Accept TLS Certificate:** Tap **Advanced** $\rightarrow$ **Proceed to `<5G_SERVER_IP>` (unsafe)**. (The self-signed certificate SAN contains the 5G server's IP address).
5. **Enter VR:** Tap the **"Enter VR"** button at the bottom of the screen. You start seated inside the rover cockpit!

### Controller Mapping

```
           LEFT CONTROLLER                           RIGHT CONTROLLER
       ┌──────────────────────┐                  ┌──────────────────────┐
       │   [Stick] Throttle   │                  │   [Stick] Steer      │
       │   (Up=Fwd, Dn=Rev)   │                  │   (Left / Right)     │
       │                      │                  │                      │
       │   (X) Toggle View    │                  │   (A) ARM Rover      │
       │   (Y) Emergency STOP │                  │   (B) Cancel Route   │
       │                      │                  │                      │
       │   [Grip] DEADMAN     │                  │   [Trigger] Laser    │
       │          CLUTCH      │                  │   Point-and-Click    │
       └──────────────────────┘                  └──────────────────────┘
```

| Action | Quest Touch Controller | Desktop Keyboard Fallback |
| :--- | :--- | :--- |
| **Deadman Clutch (Required for motion)** | Hold **Grip** (either hand) | Hold **Space** |
| **Arm / Take Control** | Press **A** (with clutch released) | Press **R** |
| **Throttle & Steering** | Left stick throttle, Right stick steer | **W**/**S** throttle, **A**/**D** steer |
| **Autonomous Waypoint Navigation** | Point right laser at map & pull **Trigger** | Left-click map table or tablet |
| **Cancel Route** | Press **B** | Press **C** |
| **Emergency Stop** | Press **Y** | Press **X** |
| **Switch View (Cockpit $\leftrightarrow$ Table)** | Press **X** | Press **V** |
| **Recenter Seating** | Click **Left Stick** | Recenter view |

> **One operator at a time:** The safety layer strictly allows one active operator. If a second page connects, it takes control and the first page displays *"another operator has control"*. Press **A** on the headset to reclaim control.

---

## 3. Scenarios & Mission Overview

| Scenario | What the operator does |
| :--- | :--- |
| **Rescue rover** (default) | A 3D town hit by disaster: **flood, earthquake, wildfire or landslide**. The experimenter switches between them live from the dashboard. Point at the map to send a 6-wheel rescue rover there (autonomous $A^*$ path planning around hazards), or grab the controls and drive manually. Bring 5 survivors back to the rescue camp. |
| **Arm** (`--scenario arm`) | Tabletop 6-DOF IK arm: pick up cubes and drop them in a bin. |

### The Four Disasters

| | Flood | Earthquake | Wildfire | Landslide |
| :--- | :--- | :--- | :--- | :--- |
| **Scene** | River bursts banks; flooded streets & submerged houses | ~35% of houses collapsed; debris blocks roads | Grass fire spreads with wind; burnt ground turns black | Mud slides down the hill across roads |
| **Impassable** | Deep water (> 0.8 m) | Rubble heaps | Fire front | Deep mud |
| **Slow** | Shallow water (40% speed) | Debris (50%) | Smoke near fire (70%) | Mud (30%) |
| **Survivors** | On roofs of flooded houses | In collapsed buildings | Down-wind of fire (airlifted if fire reaches them first) | Slide edge & on hill |
| **Dashboard** | Raise / Lower water level | **Aftershock!** (drops debris live) | **Wind shift**, **Pause**, Speed toggle | **Trigger new slide** |
| **Special** | Bridges submerge at stage 3 | Route re-plans live around rubble | Heat meter: slows >80%, stops at 100% | detours around blocked roads |

---

## 4. Why 5G? — Live Network Comparison

The dashboard (`/rescue-dashboard/`) includes a **Why 5G?** explainer (`/why5g/`) comparing real private 5G against emulated public 5G, 4G, busy Wi-Fi, and satellite:

| Profile | Added RTT | Jitter | Loss | Real-World Behavior |
| :--- | :--- | :--- | :--- | :--- |
| **Private 5G + MEC (This Lab)** | **0 ms** | **0 ms** | **0.0%** | **Sub-millisecond latency on UPF/N6 edge server.** |
| Public 5G $\rightarrow$ Cloud | +35 ms | $\pm$8 ms | 0.02% | Operator network with internet backhaul delay |
| 4G LTE $\rightarrow$ Cloud | +70 ms | $\pm$20 ms | 0.1% | Long radio scheduling delays + backhaul |
| Busy Wi-Fi | +25 ms | $\pm$40 ms | 0.3% | Contention spikes on unlicensed spectrum cause stalls |
| Satellite (GEO) $\rightarrow$ Cloud | +600 ms | $\pm$30 ms | 0.2% | Geostationary link (~36,000 km each way) |

### AI-Stop Distance Benchmark (Rover at 29 km/h)
When an obstacle or civilian appears 6.5 m in front of the rover:
- **Private 5G + MEC:** Stops safely **4.1 m** before the obstacle (31 ms total reaction time).
- **Public 5G:** Stops 3.8 m before obstacle.
- **4G LTE:** Stops 3.4 m before obstacle.
- **Busy Wi-Fi:** Stops 3.0 m before obstacle (spikes trigger watchdog holds).
- **Satellite:** **Would have hit** (travels 5.65 m blind before stop command arrives).

---

## 5. Safety Architecture (Project Specification)

| Safety Rule | Implementation |
| :--- | :--- |
| **Watchdog Deadline** | **180 ms timeout** checked at 100 Hz $\rightarrow$ rover drops to `HOLDING` (worst case under 200 ms). |
| **Sequence Protection** | Stale and out-of-order sequence numbers are rejected. |
| **Deadman Clutch** | Motion permitted **only** while clutch grip is held. |
| **Re-arm Interlock** | Transition from `HOLDING` to `ARMED` requires pressing **A** with clutch released. |
| **AI Safety Stop** | "AI may stop, never move": simulated edge detector stops rover immediately upon person detection. |
| **Geofence Enforcement** | Rover kinematic limits clamp to passable terrain and non-building cells. |

---

## 6. Access Endpoints

| Who | Rescue Rover | Arm Scenario |
| :--- | :--- | :--- |
| **VR Operator (Meta Quest)** | `https://<SERVER_IP>:8443/rescue/` | `https://<SERVER_IP>:8443/xr/` |
| **Experimenter Dashboard** | `https://<SERVER_IP>:8443/rescue-dashboard/` | `https://<SERVER_IP>:8443/dashboard/` |
| **Why 5G? Comparison** | `https://<SERVER_IP>:8443/why5g/` | — |
| **Health Check API** | `https://<SERVER_IP>:8443/api/health` | `https://<SERVER_IP>:8443/api/health` |
| **Results & Metrics API** | `https://<SERVER_IP>:8443/api/results` | `https://<SERVER_IP>:8443/api/results` |

---

## 7. Local Development & Testing

### Running Locally (macOS / Linux / Windows)
```bash
# Setup virtual environment
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run server (auto-generates TLS certificates if missing)
python -m server.main --scenario flood --port 8443

# Plain HTTP for desktop browser debugging (no WebXR)
python -m server.main --http 8080
```

### Running Test Suite (113 Tests)
```bash
pytest -v
```

### Headless 5G Link-Loss & Watchdog Trials
```bash
# Verify 200 ms watchdog trips against running server
python tools/fake_operator.py --url https://localhost:8443 --trials 10
```

---

## 8. Repository Layout

```
server/                 Simulation, safety, and MEC relay
  main.py               aiohttp web server, scenario switcher, /api/health endpoint, .env loader
  safety.py             100 Hz state machine, watchdog, out-of-order rejection
  netctl.py             5G/LTE/Wi-Fi/Satellite delay lines, jitter, TCP packet loss emulation
  certs.py              Self-signed TLS generator with IP and DNS Subject Alternative Names (SAN)
  rescue_world.py       6-wheel rover kinematics, rescue missions, obstacle clamping
  disasters.py          Flood, earthquake, wildfire, and landslide hazard simulations
  town.py               240 m x 240 m terrain, roads, houses, survivor placements
  nav.py                Fast A* pathfinder on 2D grid with path smoothing
  logger.py             CSV telemetry & run logger (commands, rtt, events, results)
  arm_world.py, sim.py  Tabletop 6-DOF IK robotic arm scenario
web/                    Client-side WebXR applications (offline, Three.js vendored)
  rescue/               Rescue rover WebXR operator (cockpit camera, mini map tablet, laser controls)
  rescue-dashboard/     Experimenter control panel (network profiles, disaster toggles, AI stops)
  why5g/                Interactive "Why 5G?" comparison, latency charts, and live stopping tests
  xr/, dashboard/       Arm scenario operator and dashboard
  vendor/               Three.js r160, VRButton (no external CDN required)
tools/
  fake_operator.py      Headless WebSocket client for automated link-loss & RTT verification
tests/                  113 unit and integration tests (pytest)
deploy.sh               One-click deployment script (SSH/rsync remote + local Docker/systemd)
Dockerfile              Production multi-stage container build (python:3.11-slim)
docker-compose.yml      Docker Compose orchestration with 5G host network support
docker-entrypoint.sh    Container entrypoint with automatic certificate generation
5g-remote-hands.service Systemd service unit template for bare-metal edge servers
.env.example            Production environment configuration template
DEPLOYMENT.md           Comprehensive 5G MEC deployment guide and operations runbook
```
