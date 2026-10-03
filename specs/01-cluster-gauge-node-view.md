# SPEC-HK-003: Automotive Instrument Cluster Gauges for Fleet & APU Nodes View

## 1. Executive Summary & Vision
The Fleet & APU Nodes view in HermesKarma provides observability into local and remote hardware nodes—primarily **`chunkito`** (AMD Strix Halo 128GB unified memory APU with Radeon 8060S gfx1151) and **`beehive`** (AMD SER8).

Currently, key APU metrics (GPU Core Load, GTT Allocation, GPU Temp, Power / TDP) are presented as four flat text cards. This specification transforms that view into a **high-precision, automotive-grade digital instrument cluster** inspired by cockpit gauge clusters (`cardash.png`).

The cluster features:
1. **Two Primary Center Dials**:
   - **Tachometer (Left Primary Dial)**: **GPU Core Load (`0 – 100%`)** with cyan radial tick graduation, dynamic redline zone (>90%), glowing high-contrast needle, and digital center readout.
   - **Speedometer (Right Primary Dial)**: **GTT Unified Memory Allocation (`0 – 128 GB` / `%`)** with precision arc graduation, active VRAM footprint indicator, and white instrument needle.
2. **Two Secondary / Outer Wing Dials**:
   - **Left Wing Dial**: **GPU Temperature (`°C`)** (Coolant temperature style: 30°C to 100°C, with cold blue zone, optimal green band 40–70°C, and warning amber/red >80°C, accompanied by temperature thermometer glyph).
   - **Right Wing Dial**: **Power / TDP (`Watts`)** (Fuel gauge style: 0W to 120W+ TDP, showing current consumption vs max package limit with lightning/plug icon).
3. **Instrument Cluster Warning & Status Telltales (Warning Lights)**:
   - ⚡ **Thermal Throttle Light**: Illuminates amber/red when `node_cooling_device_cur_state > 0` or GPU temp > 85°C.
   - ⚠️ **OOM Guard / Check Engine Light**: Illuminates red if `node_vmstat_oom_kill > 0` or memory pressure stalls detected.
   - 🏎️ **Gear / Transmission Mode**: Displays **`[P]`** (Parked / Idle, 0 active inference slots) or **`[D]`** (Drive / Generating, active prompt eval / token decode).
   - 🟢 **Turn Signal Indicators**: Animated neon-green directional arrows active during inference generation or high memory bandwidth transfer.
   - 🌐 **Link / Network Status**: Blue high-beam icon indicating Tailscale mesh connectivity and node ping latency.
4. **Digital Center Odometer & Trip Computer**:
   - **Odometer**: Total cumulative tokens processed through node (e.g. `1,284,520 TOK`).
   - **Trip Meter**: Real-time generation velocity / throughput (e.g. `38.5 tok/s`) or prompt processing prefill speed.

---

## 2. Telemetry Ingestion & Prometheus Integration

### 2.1 Prometheus Node Exporter Scraper Enhancement (`api/services/node_collector.py`)
Node `chunkito` runs Prometheus Node Exporter on port `9100` over Tailscale. `node_collector.py` currently only reads 3 raw lines. It will be expanded to parse:
- `amdgpu_busy_percent` -> `gpu_busy_percent` (0 - 100)
- `amdgpu_gtt_used_bytes` & `amdgpu_gtt_total_bytes` -> `gtt_used_gb`, `gtt_total_gb`
- `amdgpu_vram_used_bytes` & `amdgpu_vram_total_bytes` -> `vram_used_gb`, `vram_total_gb`
- `node_hwmon_power_average_watt` / `node_hwmon_power_watt` -> `power_w` (real hardware wattage, e.g. ~96W under load)
- `node_hwmon_temp_celsius` -> `temperature_c` (real hardware junction/edge temp, e.g. ~71°C under load)
- `node_cooling_device_cur_state` -> `is_throttled: bool`
- `node_vmstat_oom_kill` -> `oom_kills: int`
- `node_network_receive_bytes_total` / `transmit_bytes_total` -> `net_rx_rate`, `net_tx_rate`

### 2.2 Data Contract: Node Telemetry AMDGPU & Cluster Object
```json
{
  "amdgpu": {
    "available": true,
    "gpu_busy_percent": 95,
    "gtt_total_gb": 126.7,
    "gtt_used_gb": 83.7,
    "gtt_used_percent": 66.1,
    "vram_total_gb": 1.0,
    "vram_used_gb": 0.65,
    "vram_used_percent": 65.0,
    "power_w": 96.0,
    "power_max_w": 120.0,
    "temperature_c": 71.0,
    "temp_crit_c": 95.0,
    "is_throttled": false,
    "oom_kills": 0,
    "device_path": "AMD Radeon 8060S (Strix Halo gfx1151)"
  },
  "cluster_telltales": {
    "gear": "D",
    "active_slots": 1,
    "total_slots": 2,
    "generating": true,
    "tailscale_online": true,
    "throttle_alert": false,
    "oom_alert": false,
    "odometer_tokens": 1284520,
    "throughput_tok_s": 38.5
  }
}
```

---

## 3. Visual Design & SVG Instrument Implementation

### 3.1 SVG Cockpit Component (`api/static/app.js` & `index.html`)
The cluster will be rendered as responsive inline SVG components with CSS hardware-accelerated transitions:
- **Dimensions & ViewBox**: `viewBox="0 0 800 240"` for seamless scaling across mobile and desktop.
- **Color Palette**:
  - Background: Obsidian cockpit `#050811` with inset shadow.
  - Cyan Bezel & Ticks: `#00e5ff`, glow filter `drop-shadow(0 0 6px rgba(0, 229, 255, 0.6))`.
  - Needle 1 (GPU Core Load): High-visibility red-orange `#ff3b30` with white pivot hub.
  - Needle 2 (GTT Memory): Precision white/cyan `#e0f7fa` needle.
  - Outer Needle 3 (Temp): Needle indicating Celsius (30°C - 100°C) with cold blue to hot red gradient.
  - Outer Needle 4 (Power): Needle indicating Watts (0W - 120W).
  - Telltales: SVGs for Battery/Power, Engine/OOM, High Beam, Blinkers, and Gear selector `[P]`/`[D]`.

### 3.2 Needle Angle Interpolation Formula
Angles swept over a 220-degree arc:
- Angle range: $-110^\circ$ (min / 0) to $+110^\circ$ (max).
- $\theta = -110 + \left(\frac{\text{val} - \min}{\max - \min}\right) \times 220^\circ$.
- Rotated around dial center $(cx, cy)$ using `transform="rotate(${theta}, cx, cy)"`.

---

## 4. Verification & Testing Criteria
1. **Visual Accuracy**:
   - Layout matches `cardash.png` structure: outer wing gauges, dual center speedo/tach dials, top status telltales, bottom digital odometer.
   - Needle rotations smoothly track live values on SSE/poll updates without jumping.
2. **Prometheus Telemetry Accuracy**:
   - `chunkito` pulls live power (`~96W`) and temperature (`~71°C`) when node_exporter is reachable, falling back gracefully to sysfs or cached values when unreachable.
3. **Responsive Degradation**:
   - Works cleanly on full-width desktop monitors as well as tablet/mobile widths via SVG responsive viewBox.
4. **Automated Unit Tests**:
   - New tests in `tests/test_karma.py` validating node collector Prometheus metric parser and telltale calculations.
