# SPEC-HK-005: Cluster Gauge Telemetry Fix, Automotive Dial Realignment & Token/Speed Analytics

## 1. Executive Summary
This specification resolves feedback on HermesKarma across two core surfaces:
1. **Fleet & APU View**:
   - **Fix 0% Readout Bug**: Restore proper property binding in `renderInstrumentCluster(n)` by binding to `n.amdgpu`, `n.apu_vram`, `n.inference_engine`, and `n.hardware` instead of the string property `n.hardware.gpu`.
   - **Color Palette Restoration**: Retain the bold, thickened 10–12px arc tracks and 3px tick lines while returning to the classic automotive cockpit color palette (tachometer cyan-to-amber-to-red gradient, red tach needle, amber fuel/power gauge, coolant temperature zones, crisp white numbers with muted slate/cyan captions) instead of uniform neon cyan.
   - **Lower-Left to Lower-Right 240° Gauge Sweep**: Re-align all circular dials so that 0% starts at the lower-left (-210° / 8 o'clock), 50% sits at the top (-90° / 12 o'clock), and 100% ends at the lower-right (+30° / 4 o'clock). Digital readouts remain in the lower open quadrant below the needle sweep.

2. **Tokens & Cost View**:
   - **Output Tokens Pie Chart**: Add a dedicated doughnut/pie chart for Output Tokens specifically (`#modelOutputChart`) alongside the overall token distribution chart.
   - **Turns Visibility**: Elevate `# of turns` to a primary KPI metric card and add a dedicated "Turns" column to the Comprehensive Multi-Model Breakdown table.
   - **Creative Inference Throughput (tok/s) Display**:
     - Integrate a "Generation Velocity & Throughput" HUD panel showing measured text generation speed (TG tok/s) and prompt processing speed (PP tok/s) for local APUs and cloud frontier models.
     - Add a "Throughput (tok/s)" column in the model breakdown table with color-coded speed indicators and throughput metrics.
     - Add a Throughput Velocity Comparator chart comparing TG tok/s across the pantheon roster.

---

## 2. Technical Architecture & Calculations

### 2.1 Gauge Geometry & Alignment
Standard automotive clock sweep with 240° arc centered at the top:
- $\alpha_{\text{start}} = -210^\circ$ (Lower Left, $\cos(-210^\circ) = -0.866, \sin(-210^\circ) = +0.500$)
- $\alpha_{\text{mid}} = -90^\circ$ (Top / 12 o'clock, $\cos(-90^\circ) = 0.000, \sin(-90^\circ) = -1.000$)
- $\alpha_{\text{end}} = +30^\circ$ (Lower Right, $\cos(30^\circ) = +0.866, \sin(30^\circ) = +0.500$)

For any metric normalized to percentage $P \in [0, 100]$:
- Arc Sweep: $\theta(P) = -210^\circ + (P \times 2.4)^\circ$
- Needle Rotation: $\text{rot}(P) = -120^\circ + (P \times 2.4)^\circ$ (with vertical needle pointing north at $0^\circ$)
- Arc Path: SVG arc from $-210^\circ$ to $\theta(P)$ with `stroke-linecap="round"`
- Digital Readouts: Placed at $y = 158 - 180$ in the lower open gap between $+30^\circ$ and $+150^\circ$.

### 2.2 Color Palette Specifications
- **GPU Load (Tachometer)**:
  - Background rail: `#1e293b` (slate-800) with `#141a26` inner face
  - Track: Linear gradient `#00e5ff` (0–70%) $\to$ `#ff9100` (70–85%) $\to$ `#ff1744` (85–100% redline)
  - Needle: `#ff3b30` (tachometer red) with white pivot hub
  - Value: `#ffffff` bold monospace; label: `#94a3b8` / `#38bdf8`
- **GTT Memory (Speedometer)**:
  - Track: `#00e5ff` (cyan) with subtle glow
  - Needle: `#ffffff` needle with `#00e5ff` border and center cap
  - Value: `#ffffff` bold monospace; label: `#38bdf8`
- **Coolant Temperature**:
  - Track: `#00e5ff` (normal) $\to$ `#ff9100` (warning $\ge 70^\circ\text{C}$) $\to$ `#ff1744` (danger $\ge 85^\circ\text{C}$)
  - Needle: `#ffffff` with temperature accent
- **Power / TDP (Fuel Gauge)**:
  - Track: Amber `#ff9100` with redline $>90\%$ TDP (`#ff1744`)
  - Needle: `#ffffff` with `#ff9100` accent
- **Slots**:
  - Track: Cyan `#00e5ff` active, `#475569` idle

### 2.3 Telemetry Data Binding Resolution
In `renderInstrumentCluster(n)`:
```javascript
const gpu = n.amdgpu || {};
const apu = n.apu_vram || {};
const inf = n.inference_engine || n.ollama || {};
const hw = n.hardware || {};
const sys = n.system || n.cpu || {};
```
- GPU Core Load: `gpu.gpu_busy_percent ?? 0`
- GTT Total: `gpu.gtt_total_gb ?? apu.total_gb ?? hw.vram_gb ?? (hw.ram_gb ? 118 : 16)`
- GTT Used: `gpu.gtt_used_gb ?? apu.used_gb ?? 0`
- Temperature: `gpu.temperature_c ?? 30`
- Power: `gpu.power_w ?? 0`
- Max TDP: `sys.tdp_w ?? (hw.ram_gb > 64 ? 120 : 54)`
- Active Slots: `inf.slot_summary?.active_slots ?? 0`
- Total Slots: `inf.slot_summary?.total_slots ?? (inf.slots?.length || 2)`

### 2.4 Tokens & Cost Enhancements
1. **Output Tokens Doughnut Chart (`#modelOutputChart`)**:
   - Chart labels: Models from `data.model_distribution`
   - Dataset: `m.output_tokens` per model
   - Color-coordinated with Model Token Distribution
2. **Turns Tracking & Elevation**:
   - Update Card 1 to feature **Total Turns** as a hero stat (`${data.total_messages.toLocaleString()} Turns`), with sessions and API calls as subtitles.
   - Add a "Turns" column to the `modelsTableBody` table in `index.html`.
3. **Tok/s (Generation Velocity)**:
   - Provide empirical and telemetry-based tok/s data:
     - Local APUs: Chunkito Strix Halo (`~35–45 tok/s` decode, `~320 tok/s` prefill)
     - Cloud: Gemini 3.8 Flash (`~150 tok/s`), Gemini 3.7 Flash (`~135 tok/s`), Claude Opus (`~45 tok/s`)
   - Add "Speed (tok/s)" column in the model breakdown table with high-visibility badges.
   - Add a "Generation Velocity (tok/s)" comparator bar chart (`#velocityChart`) showing decode speed across all active models in the pantheon.

---

## 3. Implementation Plan
- **Phase 1: Backend & Data Contract**: Verify and enrich `/api/analytics/overview` with turns per model and model throughput metadata.
- **Phase 2: Gauge Redesign & Geometry Fix**: Rewrite `renderInstrumentCluster(n)` in `api/static/app.js` with corrected telemetry binding, 240° clock sweep (-210° to +30°), and restored classic automotive color palette.
- **Phase 3: Tokens & Cost View Overhaul**: Add Output Tokens doughnut chart, Turns KPI card & column, and Generation Velocity chart + table badges in `api/static/index.html` and `api/static/app.js`.
- **Phase 4: Verification & Automated Tests**: Add unit tests in `tests/test_karma.py` for new metrics and verify browser rendering.
