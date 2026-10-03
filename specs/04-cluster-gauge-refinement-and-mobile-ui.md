# SPEC-HK-004: Instrument Cluster Refinements & Mobile-Responsive UI for HermesKarma

## 1. Executive Summary
This specification details UI/UX refinements for HermesKarma based on direct user review of the automotive cluster gauges and dashboard usability across form factors:
1. **Dial Text Positioning**: Lower all dial numeric readouts and labels below the needle sweep paths so needles never obscure readouts like "96% GPU LOAD".
2. **Radial Bar & Tick Prominence**: Thicken the circular dial tracks and tick marks into high-contrast, bold instrument arcs.
3. **Dynamic Scale Normalization**:
   - **GTT Memory**: Normalized to the node's exact allocatable room (e.g., **0 – 118 GB** on Chunkito, **0 – 16 GB** on Beehive) with percentage of available room clearly displayed.
   - **Power / TDP**: Normalized from **0% to 100%** of package TDP limit (e.g., 120W on Chunkito Strix Halo, 54W on Beehive SER8), showing both current wattage and percentage.
4. **Typography & Contrast**: Apply bold, high-contrast monospace typography (`font-extrabold`, `#ffffff`, `#00e5ff`, `#ff3b30`) across all dial labels and numbers.
5. **Decluttering**:
   - Remove cryptic automotive top telltales (high beam, battery, oil warning lights).
   - Remove redundant 4-box text tiles beneath the cluster.
6. **Mobile-Responsive Architecture**:
   - Make the entire HermesKarma dashboard responsive across phones, tablets, and desktop viewports.
   - Introduce responsive mobile header with toggleable navigation drawer / scrollable tab pills.
   - Optimize grid layouts, SVG viewBoxes, and table overflow scrolling for touch devices.

---

## 2. Cluster Gauge Geometry & Visual Architecture

### 2.1 Scale Normalization Formulas
1. **GTT Memory Dial**:
   - Max capacity $M_{\text{GTT}}$:
     - Chunkito: `118.0 GB` (from hardware configuration / sysfs).
     - Beehive: `16.0 GB` (or system VRAM / GTT allocation).
   - Ratio $R_{\text{GTT}} = \min(1.0, \frac{V_{\text{used}}}{M_{\text{GTT}}})$.
   - Sweep Angle: $-120^\circ$ to $+120^\circ$ ($240^\circ$ arc).
   - Angle $\theta_{\text{GTT}} = -120^\circ + (R_{\text{GTT}} \times 240^\circ)$.
   - Primary Readout: `${V_used.toFixed(1)} / ${M_GTT} GB` with bold `${Math.round(R_GTT * 100)}% GTT POOL`.

2. **Power / TDP Dial**:
   - Max TDP $M_{\text{PWR}}$:
     - Chunkito: `120.0 W` (Radeon 8060S / Ryzen AI Max+ 395).
     - Beehive: `54.0 W` (Ryzen 7 8845HS SER8).
   - Ratio $R_{\text{PWR}} = \min(1.0, \frac{P_{\text{current}}}{M_{\text{PWR}}})$.
   - Scale: 0% at bottom to 100% at top.
   - Readout: `${P_current.toFixed(0)}W (${Math.round(R_PWR * 100)}%)`.

3. **GPU Core Load (Tachometer)**:
   - Scale: 0 to 100%.
   - Angle $\theta_{\text{GPU}} = -120^\circ + (\frac{\text{Load}}{100} \times 240^\circ)$.
   - Readout: `${Load}% GPU LOAD` positioned below needle pivot $(y=165)$.

4. **GPU Temperature**:
   - Scale: 30°C to 100°C.
   - Readout: `${Temp.toFixed(1)}°C` positioned below needle pivot.

### 2.2 Text Repositioning & Contrast
- Drop all primary digital numbers down to $y = 158 - 175$ (below pivot hub $y=110$).
- Stroke thickness of circular track increased to **10px** with layered background rail (**12px**).
- Major ticks: width **3px**, length **12px**.
- Remove redundant lower grid boxes.

---

## 3. Mobile-First Responsive Design

### 3.1 Mobile Navigation Architecture
- On screens $< 768\text{px}$ (`md` breakpoint):
  - Collapsible slide-over drawer or compact sticky bottom navigation bar with icons (`Sessions`, `Analytics`, `Pantheon`, `Nodes`, `More`).
  - Mobile hamburger button in top navbar.
  - Full touch-target size (minimum 44x44px per button).

### 3.2 View Adaptation
- **Instrument Cluster**: SVG uses `viewBox="0 0 760 210"` with `w-full h-auto` scaling naturally down to 320px screen width without clipping.
- **Tables**: Wrapped in `overflow-x-auto` with `-webkit-overflow-scrolling: touch` and scroll indicators.
- **Summary Cards**: Grid adapts from `grid-cols-4` on desktop to `grid-cols-2` on tablets and `grid-cols-1` on mobile.

---

## 4. Verification & Testing
1. Visual inspection across screen widths (desktop 1920px, tablet 768px, mobile 375px).
2. Dial text readability: Needle at 0%, 50%, 95%, and 100% does not occlude the text readouts.
3. GTT scale displays 0–118 GB on Chunkito and 0–16 GB on Beehive.
4. Power dial scale displays 0–100% of TDP.
5. All backend automated tests pass.
