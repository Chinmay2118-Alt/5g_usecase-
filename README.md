# 5G Remote Hands — VR teleoperation over private 5G (simulation)

An operator wearing a Meta Quest controls a simulated robot over the lab's private 5G network. This laptop plays two roles:

- **the robot**: simulation and the safety layer;
- **the edge/MEC relay**: WebSockets, latency injection and logging.

The Quest draws the 3D scene itself. Only commands (50 Hz) and robot state (30 Hz, about 1–2 KB, plus a ~5 KB fire map twice a second in the wildfire) cross the 5G link.

```
Quest browser (WebXR) ──Wi-Fi──► 5G AP/CPE ──► private 5G (LPRU · CU-DU · core/UPF) ──► this laptop :8443
   commands 50 Hz  ───────────────────────────────────────────────────────────────►  safety + sim (100 Hz)
   ◄─────────────────────────────────────────────────────────────────  acks + state 30 Hz
```

There are two kinds of scenario:

| Scenario | What the operator does |
| --- | --- |
| **Rescue rover** (default) | A command table in VR shows a miniature town hit by a disaster: **flood, earthquake, wildfire or landslide**. The experimenter switches between them live from the dashboard. Point at the map to send a 6-wheel rescue rover there (it plans its own route around the hazards), or grab the controls and drive it. Bring 5 survivors back to the rescue camp. |
| `arm` | Tabletop arm: pick up cubes and drop them in a bin. |

There is no robot hardware, no video stream and no ROS 2. The safety rules from the project document are kept exactly: watchdog, stale and out-of-order rejection, clutch, geofence/limits, re-arm, and "AI may stop, never move".

## 1. Setup (once)

```powershell
cd C:\Personal\5g_Usecase
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m server.certs          # self-signed cert for this laptop's IPs
```

To let the Quest reach the laptop, open the port in Windows Firewall. Run this once in an **administrator** PowerShell:

```powershell
New-NetFirewallRule -DisplayName "5G Remote Hands" -Direction Inbound -Protocol TCP -LocalPort 8443 -Action Allow -Profile Any
```

If the laptop's IP changes, regenerate the cert with `.\run.ps1 -NewCert`.

## 2. Run

```powershell
.\run.ps1                       # rescue rover, starts in the flood
.\run.ps1 -Scenario wildfire    # start in another disaster: flood, earthquake, wildfire, landslide
.\run.ps1 -Scenario arm         # tabletop arm
```

Startup takes a few seconds, because all four disaster maps are built in advance so switching is instant. The server prints the URLs. For example, at 192.168.1.184:

| Who | Rescue rover | Arm |
| --- | --- | --- |
| Operator (Quest browser) | `https://192.168.1.184:8443/rescue/` | `https://192.168.1.184:8443/xr/` |
| Dashboard (projector / experimenter) | `https://192.168.1.184:8443/rescue-dashboard/` | `https://192.168.1.184:8443/dashboard/` |
| Why 5G? (explainer + live comparison) | `https://192.168.1.184:8443/why5g/` | |

**Quest:**
1. Connect the headset to the 5G AP/router's Wi-Fi.
2. Open the operator URL in the Meta Quest Browser. You will see a certificate warning: choose **Advanced → Proceed**.
3. Press **Enter VR**. You start in the **cockpit**: seated inside the rover, looking through its front camera. Press **X** to switch to the **table view** (the miniature map) and back. Click the **left stick** to re-centre.

**One operator at a time.** If a second operator page connects (for example a laptop tab while the headset is in use), it takes control. The first page then shows **"another operator has control"** and stays off. Press **A** (or **R**) there to take control back.

**Network check first.** The Quest must reach the laptop *through the 5G network*. If the laptop and the headset are on the same 5G router's Wi-Fi, traffic may stay on the Wi-Fi and never cross the 5G core. For real 5G numbers, put the laptop on the MEC/UPF side of the network.

---

## 3. Rescue rover

**The mission (all disasters)**
1. 5 survivors wait under red **SOS** beacons.
2. Get within about 10 m of a survivor's house and **stay still for 2 s**. The survivor is loaded; a yellow ring shows the progress.
3. The rover carries **2 survivors** at a time.
4. Drive into the green **camp ring** to drop them off.
5. The mission timer runs from the first movement until every survivor is rescued (or airlifted, in the wildfire).

**Two views (X in VR, V on desktop)**
- **Cockpit** (the default in VR): you sit in the rover at life size and see the world through its front camera. Head tracking lets you look around.
  - A **map tablet** on the dashboard shows the whole area. Point the laser at it and pull the trigger to send the rover anywhere. You can also point at the ground you can see.
  - Comfort: the cockpit frame never moves relative to you, which gives a stable rest frame. The view darkens at the edges while the rover turns or brakes hard, and the horizon stays level. If anyone feels unwell, switch to the table view.
- **Table**: the miniature disaster map on a command table, with a floating **rover camera** screen (a chase view).

Both views are rendered on the headset from the 30 Hz state stream; no video crosses the 5G link.

**The four disasters**

| | Flood | Earthquake | Wildfire | Landslide |
| --- | --- | --- | --- | --- |
| **Scene** | The river has burst its banks: flooded streets and half-submerged houses | About 35% of houses have collapsed into rubble heaps; debris piles block roads | A grass fire starts in the north-west and spreads with the wind; burnt ground turns black | Mud has slid off the north-west hill across the roads |
| **Impassable** | deep water (more than 0.8 m) | rubble | fire | thick mud |
| **Slow** | shallow water (40% speed) | debris (50%) | smoke near the fire (70%) | mud (30%) |
| **Survivors** | on roofs of flooded houses | in collapsed buildings | in houses down-wind of the fire. **If the fire reaches a house before the rover does, that survivor is airlifted** (grey, not counted as rescued). | at the slide's edge and up on the hill |
| **Dashboard buttons** | Raise / Lower water (3 stages; the first bridge goes under at stage 3) | **Aftershock!**: the table shakes and 3 new debris piles drop on roads ahead of the rover's route | **Wind shift**, **Pause fire**, Fire **slow / normal / fast** | **Trigger new slide** (4 in total) |
| **Special** | — | — | **Heat meter.** Near the fire the rover heats up: above 80% it runs at half speed, and at 100% it **stops itself** (`stop_overheat`). Re-arm with **A** and drive away; it stops again only after cooling below 60%. | — |

Whenever a hazard changes, the active route is **re-planned only if it is now blocked**.

**Controls**

| Action | Quest Touch | Desktop |
| --- | --- | --- |
| Send the rover somewhere (it plans the route) | point the **right laser** at the map (table), the dashboard tablet or the ground ahead (cockpit), pull **trigger** | **left click** on the map, tablet or ground |
| Point at a survivor's house | the rover goes to the best spot next to it | same |
| Drive it yourself (takes over and cancels the route) | hold **grip**: left stick throttle, right stick turn | hold **Space** + W/S throttle, A/D turn |
| Arm / take control back | **A** | R |
| Disarm | — | T |
| Cancel route | **B** | C |
| Stop | **Y** | X |
| Switch cockpit / table view | **X** | V |
| Re-centre (seat or table) / look around | click **left stick** | right-drag look/orbit, wheel zoom (table) |

**Rover rules (enforced in the server)**

| Rule | Value |
| --- | --- |
| Speed | 8 m/s max (29 km/h), 3 m/s in reverse |
| Steering | skid steer, so it can turn on the spot |
| Geofence | the rover cannot enter buildings or impassable cells; it stops at the edge |
| Auto mode | needs the link alive and the robot armed; the headset keeps sending 50 Hz heartbeats |
| Watchdog, link cut, STOP, AI stop, overheat | **HOLDING**: emergency braking, and the route is kept; press **A** to re-arm and resume |

**Dashboard (experimenter / audience)**
- **Scenario** dropdown: switches the disaster live. The rover is disarmed, the headset map rebuilds, and a new log run starts.
- Live top-down map: hazards, houses, survivors (red waiting, yellow on board, grey airlifted), rover heading, route, and the wind arrow.
- Mission tiles and an RTT chart.
- **Added RTT** 0 / 50 / 100 / 200 / 400 ms and **Cut / Restore link**.
- The scenario's own **hazard buttons**.
- **Person crosses road (AI stop)**: a civilian appears 7 m in front of the rover for 3 s, and the simulated detector stops the rover. AI may stop, never move.
- **STOP rover**, **Reset mission** and **Start run** (participant ID).
- A **Completed missions** table: scenario, added RTT, measured RTT, rescued count and mission time.

**Demo run-sheet (about 10 min)**
1. **Flood.** Point at survivor 1 and send the rover. It plans around the deep water, stops, and loads. Take over and drive through a flooded street. Then **Raise water** twice: roads disappear and the route re-plans.
2. **Earthquake.** Switch from the dropdown. The town is now rubble. Send the rover, then hit **Aftershock!** while it drives: the table shakes, debris lands ahead, and the route re-plans live.
3. **Wildfire.** Switch, then set **Fire: fast**. Race the fire to the down-wind survivors. Drive manually close to the flames to show the heat meter, and watch the rover stop itself at 100%. Re-arm and back away. Show **Wind shift**.
4. **Landslide.** Switch, then **Trigger new slide** to cut the route to the hill survivors; the rover finds a detour.
5. **Latency.** In any scenario, add 200 ms and then 400 ms. Manual driving gets hard, but waypoints still work: **autonomy at the edge hides network latency**.
6. **Safety.** **Person crosses road** gives an AI stop. **Cut link**, or turning off the headset's Wi-Fi, brings up "LINK LOST — ROVER HOLDING". Re-arm.
7. **Results.** Show the Completed missions table.

---

## 3b. Why 5G? — live network comparison

The dashboard has a **Network** row and a **Why 5G? ↗** button (it opens `/why5g/`, also good on a projector).

**Network profiles.** Each one is emulated on top of the real private-5G link:

| Profile | Added RTT | Jitter | Loss |
| --- | --- | --- | --- |
| **Private 5G + MEC (this lab)** | none (the real link) | — | — |
| Public 5G → cloud | +35 ms | ±8 ms | 0.02% |
| 4G LTE → cloud | +70 ms | ±20 ms | 0.1% |
| Busy Wi-Fi | +25 ms | spiky ±40 ms | 0.3% |
| Satellite (GEO) → cloud | +600 ms | ±30 ms | 0.2% |

- The emulation behaves like TCP, which WebSocket runs on: messages stay in order, so a late message holds up everything behind it. A lost packet is a 200 ms retransmission stall, which is longer than the watchdog, so on lossy networks the rover's safety layer occasionally holds.
- All values are typical published ranges, for illustration. They live in `server/netctl.py`.

**The AI-stop distance test.**
1. Drive at full speed.
2. Click **Person crosses road**. The person appears 6.5 m ahead of the bumper.
3. The stop only takes effect after the **AI pipeline latency** of the active network: camera frame up to the AI, 30 ms inference, stop command back down. For private 5G, that's the round trip the headset actually measures.
4. The dashboard and HUD then report the reaction time, the distance **driven blind** before the stop arrived, the stopping margin, and **"stopped"** or **"would have hit"**.

Measured end to end on this laptop at 29 km/h:

| Network | AI stop after | Driven blind | Result |
| --- | --- | --- | --- |
| Private 5G + MEC | 31 ms | 0.25 m | stopped **4.1 m** before the person |
| Public 5G → cloud | 72 ms | 0.57 m | stopped 3.8 m before |
| 4G LTE → cloud | 124 ms | 0.99 m | stopped 3.4 m before |
| Busy Wi-Fi | 169 ms (spiky) | 1.36 m | stopped 3.0 m before |
| Satellite (GEO) | 706 ms | 5.65 m | **would have hit** |

**The `/why5g/` page** has these sections:
- what a rescue robot needs from the network;
- six cards linking 5G features to the demo: URLLC, MEC, eMBB uplink, QoS/slicing, private-network security, mobility;
- a comparison table;
- a "where the time goes" latency-budget chart;
- a "latency becomes metres" stopping chart;
- the architecture diagram, with the live measured RTT;
- a **live results table** that fills in with every stop test and completed mission from the session, grouped by network. Missions also record **collisions** (bumps into obstacles), a good measure of manual-driving difficulty under latency.

**Run-sheet (about 4 min)**
1. Network: **Private 5G + MEC**. Drive fast and click **Person crosses road** → stopped about 4 m short.
2. Repeat on **4G**, **Busy Wi-Fi** and **Satellite** → satellite: "would have hit".
3. Stay on Satellite and drive manually: it is almost impossible. Then send a waypoint: the rover still gets there. **Autonomy at the edge hides latency.**
4. Open **Why 5G?**. The results you just produced are in the table, next to the charts.

**Talking points**
- *"Latency becomes metres."* At 29 km/h, every millisecond is 8 mm driven blind. The edge keeps the AI stop within a few centimetres; a cloud over satellite costs over 5 m.
- *"It's not only average latency."* Busy Wi-Fi has a decent average, but its spikes and stalls make the safety layer hold the rover. 5G's scheduled radio and QoS keep control traffic predictable.
- *"MEC is half the story."* Even public 5G loses most of its advantage once traffic has to cross the internet to a cloud region. A local UPF with an edge server keeps the loop on site.
- *"Safety is designed for the bad case."* The watchdog stops the rover within 200 ms of a lost link on any network. 5G makes that case rare.

---

## 4. Arm scenario (`.\run.ps1 -Scenario arm`)

**Task:** put 2 cubes in the bin.

| Action | Quest | Keyboard |
| --- | --- | --- |
| Clutch: the robot moves only while held | hold **grip** | hold **Space** |
| Move left/right, toward/away | left stick | A/D, W/S |
| Move up/down | right stick | E/Q |
| Close the gripper | hold **right trigger** | hold **F** |
| Arm / disarm / stop | **A** / **B** / **Y** | R / T / X |

---

## 5. Safety behaviour (all scenarios)

| Rule | Value |
| --- | --- |
| Watchdog: no valid command → **HOLDING** | 180 ms timeout, checked at 100 Hz (worst case under 200 ms) |
| Re-arm needed after any hold | **A**, only with the grip released |
| Out-of-order or duplicate sequence numbers | rejected |
| Command delivered more than 150 ms late through the delay line | dropped (`stale`) |
| Stop from operator, dashboard, AI, overheat or link drop | → HOLDING |
| Headset page hidden or VR exited | the client stops sending → watchdog holds |

**Added RTT** is split half uplink, half downlink. It stands in for `tc netem`, which Windows does not have.

**Simulated parts.** The edge-AI detections (survivors, the person on the road) are simulation ground truth plus noise, standing in for YOLO on the MEC. The rover camera is rendered on the headset. The fire is a deterministic spread model (faster down-wind, slower up-wind and across roads, stopped by water), so demos are repeatable.

## 6. Logs

Each run writes `logs/<run_id>/`. Switching scenario starts a new run.

| File | Contents |
| --- | --- |
| `run.json` | scenario, participant, label, added delay at start |
| `commands.csv` | every command: time, seq, accepted, reason, state, input, added RTT |
| `rtt.csv` | RTT p50/p95/last reported by the Quest every second |
| `events.csv` | state changes, plus `scenario`, `goto`, `route_end`, `replanned`, `blocked`, `loaded`, `rescued`, `airlifted`, `hazard` (each button and its effect), `overheat`/`cooled`, `person_on_road`, `task_complete`; and delay and link changes |

## 7. Tests

```powershell
.\.venv\Scripts\python -m pytest -q             # 110 tests: safety, protocol, arm, town, planner, rover, disasters, network comparison
.\.venv\Scripts\python tools\fake_operator.py   # 20 link-loss trials against the running server
```

For every disaster, the tests run a full autonomous mission (all 5 survivors rescued) and check that routes never cross impassable cells. They also cover:
- aftershocks re-planning the route;
- landslides blocking roads;
- fire spread, burn-out, wind shift and pause;
- overheating, cooling and re-arm;
- airlifts;
- the fast planner grid matching the reference implementation;
- live scenario switching.

## Interface (JSON over WebSocket)

| Message | Direction | Fields |
| --- | --- | --- |
| `command` | Quest → server, 50 Hz | `seq`, `t_op`, `clutch`, `vx`, `vy`, `vz` (−1..1), `gripper`. Rover: `vz` = throttle (−1 = forward), `vx` = turn (+1 = right) |
| `goto` / `cancel_goto` | Quest → server | `x`, `z` in map metres (rescue only) |
| `ack` | server → Quest, per command | `seq`, `t_op` (echoed, so RTT uses only the Quest's clock), `state` |
| `hello` | server → clients on connect and on every scenario switch | `scenario`, `title`, `scenarios`, `controls`, `map` (town, survivors, hazard data) or `geometry` (arm) |
| `state` | server → Quest 30 Hz, dashboard 10 Hz | `robot`, `sim` (scenario snapshot with `hz` hazard data), `net`, `run`, `det`; `layer` (fire cells, 2 bits each, base64) when it changes |
| `control` | Quest: `arm`, `disarm`, `stop`. Dashboard: `stop`, `disarm`, `set_delay_ms`, `cut_link`, `reset_task`, `start_run`, `set_scenario`, `hazard` (value = button id), `person_on_road`, `intruder` | |

**Map frame:** metres, Y up, x and z in [−120, 120], +Z towards the operator (south), +X to the operator's right. The rover's `yaw` is 0 facing +Z, with forward = (sin yaw, cos yaw).

## Layout

```
server/   main.py (server, live scenario switching) · safety.py · netctl.py · protocol.py · logger.py · certs.py
          rescue_world.py (rover, missions) · town.py (terrain, roads, houses) · disasters.py (flood, earthquake,
          wildfire, landslide) · nav.py (A* + smoothing)
          arm_world.py + sim.py (tabletop arm)
web/rescue/            WebXR operator: cockpit.js (front-camera view + map tablet), map table, hazards.js
                       (rubble / fire / mud), rover camera, laser pointing, HUD
web/common/topdown.js  top-down map drawing shared by the dashboard and the cockpit tablet
web/rescue-dashboard/  command dashboard (network profiles, AI-stop result card)
web/why5g/             "Why 5G?" explainer + live comparison page
web/xr/, web/dashboard/  arm scenario
web/common/, web/vendor/ (Three.js r160, vendored; no internet needed)
tools/fake_operator.py   headless operator + link-loss trials
tests/
```
#   5 g _ u s e c a s e -  
 