/**
 * Homelab 3D Spatial Visualizer — Procedural Cyber-Chassis Scene (SPEC-HK-009)
 * ---------------------------------------------------------------------------
 * TASK-HK-138 (Phase 2): Procedural Translucent Cyber-Chassis Geometries with
 * Internal Living Silicon Cores.
 * TASK-HK-139 (Phase 3): Dynamic 3D Bezier Conduits, HomelabFlowParticleSystem
 * (single 256-instance InstancedMesh photon engine), and the Tailscale
 * WireGuard mesh datum lattice with live ping RTT band indicators.
 *
 * Zero-asset architecture (SPEC-HK-009 §9.5): every geometry, material, and
 * texture in this file is generated procedurally via core Three.js (r160)
 * primitives. No glTF/OBJ/model downloads ever occur.
 *
 * Universal open-source hardware archetype taxonomy (SPEC-HK-009 §3):
 *   mini_pc      — Beelink SER/EQ, NUC, Mac Mini, Geekom squircle workhorse
 *   tower        — ATX/SFF workstation, MS-S1 Max, PCIe accelerator rigs
 *   phone        — Pixel / iPhone / Android glass slab endpoints
 *   laptop       — clamshell / handheld dev workstations (Win Max 2, Deck)
 *   tablet       — digital-paper / touch slates (reMarkable 2, iPad)
 *   server_rack  — 1U/2U horizontal rackmount chassis
 * Bespoke homelab presets (chassis_variant, §3.3): beelink_ser8, beelink_eqi,
 * minisforum_ms_s1_max, gpd_win_max_2, remarkable_2, pixel_9_pro — unknown
 * variants gracefully fall back to their parent archetype.
 *
 * Loading: UMD-style. In the browser this attaches
 *   window.HomelabVisualizerScene  (scene manager class)
 *   window.HomelabVisualizerGeometry (procedural factory registry)
 * and resolves the THREE namespace from `window.THREE` at construction time,
 * so the page may load Three r160 as an ES module first and assign the global.
 * In Node (CI headless verification) `require('./visualizer_scene.js')`
 * returns the same registry; call `.setTHREE(require('three'))` first.
 *
 * SECURITY (blueprint §5): strictly read-only visualization; consumes only
 * sanitized logical-ID topology/telemetry payloads (no hostnames/IPs).
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api; // Node / headless CI
  }
  if (root) {
    root.HomelabVisualizerGeometry = api;
    root.HomelabVisualizerScene = api.HomelabVisualizerScene;
    root.HOMELAB_VISUALIZER_EXPORTS = api.EXPORTS.slice();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // -------------------------------------------------------------------------
  // THREE namespace resolution (lazy: window.THREE may be assigned after load)
  // -------------------------------------------------------------------------
  var _three = null;
  function setTHREE(ns) { _three = ns; return api; }
  function _T() {
    var ns = _three ||
      (typeof window !== 'undefined' && window.THREE) ||
      (typeof globalThis !== 'undefined' && globalThis.THREE);
    if (!ns) {
      throw new Error(
        'HomelabVisualizer: THREE namespace not found. Load Three.js r160 ' +
        'and assign window.THREE (or call setTHREE(THREE)) before use.');
    }
    return ns;
  }

  // -------------------------------------------------------------------------
  // Palette & state semantics (SPEC-HK-009 §4.1, §5.1, §5.2)
  // -------------------------------------------------------------------------
  var PALETTE = {
    accentCyan:    '#38bdf8',
    dieGlow:       '#00ffcc',
    dieGlowWarm:   '#f59e0b',
    slateDeep:     '#1e293b',
    obsidian:      '#0a0f16',
    shellBase:     '#0f172a',
    pcbSubstrate:  '#0b1e2a',
    pcbTrace:      '#0e7490',
    ramGreen:      '#10b981',
    slotIdle:      '#1e293b',
    slotPrefill:   '#f59e0b',
    slotDecode:    '#10b981',
    slotExclusive: '#a855f7',
    ledActive:     '#22c55e',
    ledDegraded:   '#f59e0b',
    ledError:      '#ef4444',
    ledOffline:    '#64748b',
    vramLow:       '#22d3ee',   // < 70 %
    vramMid:       '#f59e0b',   // 70–90 %
    vramHigh:      '#ef4444',   // > 90 % redline
    offlineHalo:   '#ef4444',
    paperWhite:    '#e2e8f0',
    slateScreen:   '#334155',
    // Phase 3 conduit semantics (SPEC-HK-009 §4.1)
    conduitCyan:   '#00ffcc',
    conduitViolet: '#8b5cf6',
    conduitEmerald:'#10b981',
    conduitAmber:  '#f59e0b',
    conduitCrimson:'#ef4444',
    meshSlate:     '#334155',
    // Tailscale WireGuard ping RTT bands (mesh-datum-lattice)
    pingGood:      '#22c55e',   // < 15 ms
    pingWarn:      '#f59e0b',   // 15 - 50 ms
    pingBad:       '#ef4444',   // > 50 ms
    pingUnknown:   '#334155'
  };

  var SERVICE_LED_BY_STATUS = {
    active: PALETTE.ledActive,
    healthy: PALETTE.ledActive,
    degraded: PALETTE.ledDegraded,
    warning: PALETTE.ledDegraded,
    high_latency: PALETTE.ledDegraded,
    error: PALETTE.ledError,
    down: PALETTE.ledError,
    offline: PALETTE.ledOffline,
    suspended: PALETTE.ledOffline
  };

  // --------------------------------------------------------------------------
  // Preset variant -> parent archetype registry (mirrors visualizer_service.py,
  // SPEC-HK-009 §3.3). Unknown variants fall back to the parent archetype;
  // unknown archetype strings fall back to the universal mini_pc default.
  // --------------------------------------------------------------------------
  var VARIANT_TO_ARCHETYPE = {
    beelink_ser8: 'mini_pc',
    beelink_eqi: 'mini_pc',
    minisforum_ms_s1_max: 'tower',
    generic_tower: 'tower',
    gpd_win_max_2: 'laptop',
    generic_laptop: 'laptop',
    remarkable_2: 'tablet',
    generic_tablet: 'tablet',
    pixel_9_pro: 'phone',
    generic_phone: 'phone'
  };
  var VALID_ARCHETYPES = ['mini_pc', 'tower', 'phone', 'laptop', 'tablet', 'server_rack'];

  /**
   * Resolve a chassis_type string (archetype OR bespoke preset variant) into
   * { archetype, variant }. Never throws — dynamic fallback per §3.3.
   */
  function resolveChassis(chassisType) {
    var t = String(chassisType || '').toLowerCase();
    if (VALID_ARCHETYPES.indexOf(t) !== -1) return { archetype: t, variant: t };
    if (VARIANT_TO_ARCHETYPE[t]) return { archetype: VARIANT_TO_ARCHETYPE[t], variant: t };
    return { archetype: 'mini_pc', variant: t || 'mini_pc' }; // graceful default
  }

  // --------------------------------------------------------------------------
  // Small math helpers
  // --------------------------------------------------------------------------
  function clamp(v, lo, hi) { v = Number(v); if (isNaN(v) || v === null) return lo; return v < lo ? lo : (v > hi ? hi : v); }
  function lerp(a, b, k) { return a + (b - a) * clamp(k, 0, 1); }
  function color(hexOrNumber) {
    var C = _T().Color;
    return (typeof hexOrNumber === 'number') ? new C(hexOrNumber) : new C(hexOrNumber || '#000000');
  }

  // --------------------------------------------------------------------------
  // Cyber-material factories (blueprint §3)
  // --------------------------------------------------------------------------

  /**
   * Translucent frosted cyber-chassis shell (MeshPhysicalMaterial).
   * Defaults per SPEC-HK-009 task gate: transmission 0.65, thickness 0.8,
   * roughness 0.15, opacity 0.82. Archetypes override for personality
   * (e.g. mini_pc frosted silver-aluminum: transmission 0.55, opacity 0.78).
   */
  function createCyberChassisMaterial(opts) {
    opts = opts || {};
    var T = _T();
    var mat = new T.MeshPhysicalMaterial({
      color: color(opts.color || PALETTE.shellBase),
      metalness: opts.metalness !== undefined ? opts.metalness : 0.2,
      roughness: opts.roughness !== undefined ? opts.roughness : 0.15,
      transmission: opts.transmission !== undefined ? opts.transmission : 0.65,
      thickness: opts.thickness !== undefined ? opts.thickness : 0.8,
      ior: opts.ior !== undefined ? opts.ior : 1.45,
      transparent: true,
      opacity: opts.opacity !== undefined ? opts.opacity : 0.82,
      reflectivity: opts.reflectivity !== undefined ? opts.reflectivity : 0.5,
      clearcoat: opts.clearcoat !== undefined ? opts.clearcoat : 0.3,
      clearcoatRoughness: opts.clearcoatRoughness !== undefined ? opts.clearcoatRoughness : 0.1
    });
    if (opts.emissive) {
      mat.emissive = color(opts.emissive);
      mat.emissiveIntensity = opts.emissiveIntensity !== undefined ? opts.emissiveIntensity : 0.12;
    }
    mat.userData = { baseColor: mat.color.clone(), baseOpacity: mat.opacity, isShell: true };
    return mat;
  }

  /**
   * Glowing silicon die (MeshStandardMaterial, emissive modulated by load
   * 0.3 -> 1.5 at runtime via HomelabVisualizerScene.updateTelemetry).
   */
  function createSiliconDieMaterial(baseColor) {
    var T = _T();
    var c = color(baseColor || PALETTE.dieGlow);
    return new T.MeshStandardMaterial({
      color: c,
      emissive: c.clone(),
      emissiveIntensity: 0.3,
      roughness: 0.1,
      metalness: 0.9
    });
  }

  /** Brushed / matte structural metal for frames, rails, rack ears, bands. */
  function createMetalMaterial(opts) {
    opts = opts || {};
    var T = _T();
    return new T.MeshStandardMaterial({
      color: color(opts.color || '#334155'),
      metalness: opts.metalness !== undefined ? opts.metalness : 0.85,
      roughness: opts.roughness !== undefined ? opts.roughness : 0.38,
      transparent: !!opts.transparent,
      opacity: opts.opacity !== undefined ? opts.opacity : 1.0,
      emissive: color(opts.emissive || '#000000'),
      emissiveIntensity: opts.emissiveIntensity !== undefined ? opts.emissiveIntensity : 0.0
    });
  }

  /** Emissive accent / LED / glow strip material. */
  function createGlowMaterial(hex, intensity) {
    var T = _T();
    var c = color(hex || PALETTE.accentCyan);
    return new T.MeshStandardMaterial({
      color: c, emissive: c.clone(),
      emissiveIntensity: intensity !== undefined ? intensity : 0.9,
      roughness: 0.3, metalness: 0.1,
      transparent: true, opacity: 0.95
    });
  }

  /** Smoked glass viewport window material. */
  function createSmokedGlassMaterial(opts) {
    opts = opts || {};
    var T = _T();
    return new T.MeshPhysicalMaterial({
      color: color(opts.color || '#0b1220'),
      metalness: 0.1, roughness: opts.roughness !== undefined ? opts.roughness : 0.12,
      transmission: opts.transmission !== undefined ? opts.transmission : 0.8,
      thickness: 0.35, ior: 1.5,
      transparent: true, opacity: 0.55,
      clearcoat: 0.6, clearcoatRoughness: 0.08
    });
  }

  // --------------------------------------------------------------------------
  // Procedural geometry helpers
  // --------------------------------------------------------------------------

  /**
   * Squircle / rounded-box chassis geometry (core Three.js only — no addons).
   * Rounded-corner Shape extruded along the height axis with a top/bottom
   * bevel, then centered on the group origin.
   */
  function roundedBoxGeometry(w, h, d, r) {
    var T = _T();
    // Outer bbox comes out at EXACTLY w x h x d: ExtrudeGeometry bevels grow
    // OUTWARD from the source shape, so the shape is inset by the bevel size.
    var bev = Math.min((r || 0.05) * 0.6, h * 0.24);
    r = Math.max(0.001, Math.min(r || 0.05, Math.min(w, d) / 2 - 0.001));
    var W = Math.max(0.002, w - 2 * bev), D = Math.max(0.002, d - 2 * bev);
    var rr = Math.max(0.001, Math.min(r - bev, Math.min(W, D) / 2 - 0.001));
    var x = -W / 2, z = -D / 2;
    var shape = new T.Shape();
    shape.moveTo(x + rr, z);
    shape.lineTo(x + W - rr, z);
    shape.quadraticCurveTo(x + W, z, x + W, z + rr);
    shape.lineTo(x + W, z + D - rr);
    shape.quadraticCurveTo(x + W, z + D, x + W - rr, z + D);
    shape.lineTo(x + rr, z + D);
    shape.quadraticCurveTo(x, z + D, x, z + D - rr);
    shape.lineTo(x, z + rr);
    shape.quadraticCurveTo(x, z, x + rr, z);
    var geo = new T.ExtrudeGeometry(shape, {
      depth: Math.max(0.001, h - 2 * bev),
      bevelEnabled: true,
      bevelThickness: bev,
      bevelSize: bev,
      bevelOffset: 0,
      bevelSegments: 3,
      curveSegments: 6
    });
    geo.rotateX(-Math.PI / 2); // extrusion Z -> world Y (height)
    geo.center();
    return geo;
  }

  /** Flat hexagonal prism (docked service chips). Axis = Y. */
  function hexPrismGeometry(radius, height, segments) {
    var T = _T();
    return new T.CylinderGeometry(radius, radius, height, segments || 6);
  }

  /** Cooling fan assembly (hub + 5 blades); userData.spin for scene tick. */
  function buildFanAssembly(radius, colorHex) {
    var T = _T();
    var g = new T.Group();
    var metal = createMetalMaterial({ color: '#1f2937', roughness: 0.5 });
    var hub = new T.Mesh(new T.CylinderGeometry(radius * 0.24, radius * 0.24, radius * 0.18, 12), metal);
    hub.rotation.x = Math.PI / 2;
    g.add(hub);
    var bladeMat = createMetalMaterial({ color: colorHex || '#374151', metalness: 0.7, roughness: 0.45 });
    for (var i = 0; i < 5; i++) {
      var blade = new T.Mesh(new T.BoxGeometry(radius * 0.78, radius * 0.16, radius * 0.3), bladeMat);
      blade.position.set(radius * 0.48, 0, 0);
      blade.rotation.z = 0.55;
      var pivot = new T.Group();
      pivot.add(blade);
      pivot.rotation.z = (i / 5) * Math.PI * 2;
      g.add(pivot);
    }
    var frame = new T.Mesh(
      new T.TorusGeometry(radius * 0.98, radius * 0.06, 6, 20),
      createMetalMaterial({ color: '#111827', roughness: 0.6 }));
    g.add(frame);
    g.userData.spin = true;
    return g;
  }

  /** Procedural 64x64 DataTexture — faint terminal/code grid luminescence. */
  function terminalGridTexture() {
    var T = _T();
    var w = 64, h = 64;
    var data = new Uint8Array(w * h * 4);
    for (var y = 0; y < h; y++) {
      var rowOn = (y % 6) < 3; // banded "text lines"
      for (var x = 0; x < w; x++) {
        var i = (y * w + x) * 4;
        var v = 8; // near-black base
        if (rowOn) {
          var cell = Math.floor(x / 4) + Math.floor(y / 6) * 7;
          v += (cell * 53) % 11 === 0 ? 92 : ((cell * 29) % 7 === 0 ? 38 : 0);
        }
        data[i] = Math.round(v * 0.25);           // R (cyan-dominant glow)
        data[i + 1] = v;                          // G
        data[i + 2] = Math.round(v * 1.05);       // B
        data[i + 3] = 255;
      }
    }
    var tex = new T.DataTexture(data, w, h, T.RGBAFormat);
    tex.needsUpdate = true;
    tex.colorSpace = T.SRGBColorSpace;
    return tex;
  }

  /** Procedural paper-grain DataTexture for e-ink CANVAS styling. */
  function paperGrainTexture() {
    var T = _T();
    var w = 64, h = 64;
    var data = new Uint8Array(w * h * 4);
    var seed = 12345;
    function rnd() { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; }
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        var i = (y * w + x) * 4;
        var speckle = rnd() < 0.10 ? -14 : 0;
        var base = 226 + ((x * 31 + y * 17) % 9) + speckle;
        data[i] = base; data[i + 1] = base - 2; data[i + 2] = base - 8; data[i + 3] = 255;
      }
    }
    var tex = new T.DataTexture(data, w, h, T.RGBAFormat);
    tex.needsUpdate = true;
    tex.colorSpace = T.SRGBColorSpace;
    return tex;
  }

  // --------------------------------------------------------------------------
  // Interior "living silicon" sub-assemblies (blueprint §1 InteriorSilicon)
  // All positions are LOCAL to the node chassis group (origin = chassis center;
  // the backend places chassis centers at y = H/2 so chassis bottoms rest on
  // the Y = 0 datum plane).
  // --------------------------------------------------------------------------

  /** Motherboard substrate plane with glowing trace strips. */
  function addMotherboardSubstrate(group, w, d, y, traceColor) {
    var T = _T();
    var board = new T.Mesh(
      new T.BoxGeometry(w, 0.02, d),
      new T.MeshStandardMaterial({
        color: color(PALETTE.pcbSubstrate),
        emissive: color(traceColor || PALETTE.pcbTrace),
        emissiveIntensity: 0.18, roughness: 0.62, metalness: 0.2
      }));
    board.position.y = y;
    group.add(board);
    var traceMat = createGlowMaterial(traceColor || PALETTE.pcbTrace, 0.45);
    for (var i = 0; i < 3; i++) {
      var trace = new T.Mesh(new T.BoxGeometry(w * 0.86, 0.008, 0.012), traceMat);
      trace.position.set(0, y + 0.016, -d * 0.32 + i * d * 0.32);
      group.add(trace);
    }
    return board;
  }

  /** DDR5 SODIMM stick with chip bumps; faint emerald memory glow. */
  function addRamSodimm(group, x, z, y, length, accentHex) {
    var T = _T();
    var g = new T.Group();
    var stickMat = new T.MeshStandardMaterial({
      color: color('#0e2f22'),
      emissive: color(accentHex || PALETTE.ramGreen),
      emissiveIntensity: 0.16, roughness: 0.5, metalness: 0.25
    });
    var stick = new T.Mesh(new T.BoxGeometry(length, 0.22, 0.05), stickMat);
    g.add(stick);
    var chipMat = createMetalMaterial({ color: '#0b1220', roughness: 0.35 });
    var nChips = Math.max(2, Math.floor(length / 0.3));
    for (var i = 0; i < nChips; i++) {
      var chip = new T.Mesh(new T.BoxGeometry(length / nChips * 0.62, 0.04, 0.055), chipMat);
      chip.position.x = -length / 2 + (i + 0.5) * (length / nChips);
      g.add(chip);
    }
    var lip = new T.Mesh(new T.BoxGeometry(length, 0.02, 0.056), createGlowMaterial(accentHex || PALETTE.ramGreen, 0.55));
    lip.position.y = 0.115;
    g.add(lip);
    g.position.set(x, y, z);
    g.userData.ramGlow = stickMat;
    group.add(g);
    return g;
  }

  /**
   * Central CPU/APU silicon die: substrate + emissive die top.
   * The die-top material is registered in group.userData.die so the scene can
   * pulse emissive intensity with live compute load (SPEC-HK-009 §5.1).
   */
  function addApuDIE(group, size, y, glowColor, label) {
    var T = _T();
    var g = new T.Group();
    var substrate = new T.Mesh(
      new T.BoxGeometry(size * 1.24, 0.035, size * 1.24),
      createMetalMaterial({ color: '#1e293b', roughness: 0.45 }));
    substrate.position.y = -0.028;
    g.add(substrate);
    var dieMat = createSiliconDieMaterial(glowColor || PALETTE.dieGlow);
    var die = new T.Mesh(new T.BoxGeometry(size, 0.05, size), dieMat);
    g.add(die);
    // micro die-grid etching (4 quadrants, faint)
    var etch = createGlowMaterial('#062a17', 0.0);
    var etchMesh = new T.Mesh(new T.BoxGeometry(size * 0.66, 0.052, size * 0.012), etch);
    g.add(etchMesh);
    var etchMesh2 = new T.Mesh(new T.BoxGeometry(size * 0.012, 0.052, size * 0.66), etch);
    g.add(etchMesh2);
    g.position.y = y;
    g.userData.die = dieMat;
    g.userData.dieLabel = label || 'APU';
    group.add(g);
    if (!group.userData.die) group.userData.die = dieMat;
    return g;
  }

  /**
   * 3D volumetric VRAM allocation bar (SPEC-HK-009 §5.1): glass housing with
   * an inner fill column scaled by used/total. Scene recolors the fill:
   * cyan < 70 %, amber 70–90 %, redline > 90 %.
   */
  function addVramBar(group, x, z, height, width, yBase) {
    var T = _T();
    var w = width || 0.26;
    var g = new T.Group();
    var housing = new T.Mesh(
      new T.BoxGeometry(w, height, w),
      new T.MeshPhysicalMaterial({
        color: color('#0b1220'), metalness: 0.1, roughness: 0.1,
        transmission: 0.9, thickness: 0.2, ior: 1.48,
        transparent: true, opacity: 0.28
      }));
    housing.position.y = height / 2;
    g.add(housing);
    // fill anchored at bottom (origin at base) so scale.y grows upward
    var fillMat = new T.MeshStandardMaterial({
      color: color(PALETTE.vramLow), emissive: color(PALETTE.vramLow),
      emissiveIntensity: 0.85, roughness: 0.25, metalness: 0.1,
      transparent: true, opacity: 0.9
    });
    var fillGeo = new T.BoxGeometry(w * 0.72, height, w * 0.72);
    fillGeo.translate(0, height / 2, 0);
    var fill = new T.Mesh(fillGeo, fillMat);
    fill.scale.y = 0.02;
    g.add(fill);
    var capMat = createGlowMaterial(PALETTE.accentCyan, 0.5);
    var cap = new T.Mesh(new T.BoxGeometry(w * 1.1, 0.02, w * 1.1), capMat);
    cap.position.y = height + 0.015;
    g.add(cap);
    g.position.set(x, yBase || 0, z); // base sits on chassis floor (caller offset)
    g.userData.vramBar = { fill: fill, fillMat: fillMat, housing: housing, height: height };
    group.add(g);
    if (!group.userData.vramBar) group.userData.vramBar = g.userData.vramBar;
    return g;
  }

  /**
   * Ring of inference-slot micro-cubes surrounding the die
   * (Chunkito's Strix Halo 12-slot array, SPEC-HK-009 §5.1).
   * Statuses: idle #1e293b | prefill #f59e0b | decoding #10b981 | exclusive #a855f7
   */
  function addSlotRing(group, count, radius, y, cubeSize) {
    var T = _T();
    var n = count || 12;
    var s = cubeSize || 0.16;
    var slots = [];
    for (var i = 0; i < n; i++) {
      var a = (i / n) * Math.PI * 2;
      var mat = new T.MeshStandardMaterial({
        color: color(PALETTE.slotIdle), emissive: color(PALETTE.slotIdle),
        emissiveIntensity: 0.22, roughness: 0.35, metalness: 0.2,
        transparent: true, opacity: 0.92
      });
      var cube = new T.Mesh(new T.BoxGeometry(s, s, s), mat);
      cube.position.set(Math.cos(a) * radius, y, Math.sin(a) * radius);
      cube.userData.slotIndex = i;
      group.add(cube);
      slots.push({ mesh: cube, mat: mat, targetColor: color(PALETTE.slotIdle), targetIntensity: 0.22 });
    }
    if (!group.userData.slots || !group.userData.slots.length) group.userData.slots = slots;
    else group.userData.slots = group.userData.slots.concat(slots);
    return slots;
  }

  /**
   * Static peripheral memory blocks (non-variant towers): 12 dim DIMM-like
   * blocks flanking the motherboard, distinct from live slot cubes.
   */
  function addPeripheralMemoryBlocks(group, count, w, d, y) {
    var T = _T();
    var n = count || 12;
    var mat = new T.MeshStandardMaterial({
      color: color('#16282b'), emissive: color(PALETTE.ramGreen),
      emissiveIntensity: 0.06, roughness: 0.55, metalness: 0.3,
      transparent: true, opacity: 0.9
    });
    var blocks = [];
    for (var i = 0; i < n; i++) {
      var side = i % 2 === 0 ? -1 : 1;
      var idx = Math.floor(i / 2);
      var b = new T.Mesh(new T.BoxGeometry(0.14, 0.2, d * 0.34), mat.clone());
      b.position.set(side * (w * 0.30 + idx * 0.045), y + 0.1, (idx % 3 - 1) * d * 0.36);
      group.add(b);
      blocks.push(b);
    }
    group.userData.memoryBlocks = blocks;
    return blocks;
  }

  /**
   * Docked hexagonal service chips hovering above the chassis with pulsing
   * micro-LED status dots (SPEC-HK-009 §5.2). `services` = topology payload
   * service records ({id, name, port, icon, status}).
   */
  function attachServiceChips(group, services, opts) {
    opts = opts || {};
    var T = _T();
    var list = (services || []).slice(0, opts.maxChips || 8);
    var chipsGroup = new T.Group();
    chipsGroup.name = 'service-chips';
    var topY = (group.userData.topY || 0.5) + 0.42;
    var n = list.length;
    var spacing = Math.min(0.44, (opts.spreadWidth || 2.2) / Math.max(n, 1));
    var chipMat = createCyberChassisMaterial({
      color: '#131c26', transmission: 0.35, opacity: 0.9,
      emissive: opts.accentColor || PALETTE.accentCyan, emissiveIntensity: 0.10,
      roughness: 0.28
    });
    var leds = [];
    for (var i = 0; i < n; i++) {
      var svc = list[i] || {};
      var chip = new T.Mesh(hexPrismGeometry(0.13, 0.035, 6), chipMat.clone());
      var xOff = (i - (n - 1) / 2) * spacing;
      var bob = Math.sin(i * 1.7) * 0.03;
      chip.position.set(xOff, topY + bob, -spacing * 0.15 * ((i % 2) ? 1 : -1));
      chip.userData.serviceId = svc.id || ('svc_' + i);
      chip.userData.serviceName = svc.name || svc.id || 'service';
      chipsGroup.add(chip);
      var ledMat = createGlowMaterial(SERVICE_LED_BY_STATUS[String(svc.status || 'active').toLowerCase()] || PALETTE.ledOffline, 1.1);
      var led = new T.Mesh(new T.SphereGeometry(0.028, 8, 8), ledMat);
      led.position.set(xOff + 0.09, topY + bob + 0.03, 0.09);
      chipsGroup.add(led);
      leds.push({ mesh: led, mat: ledMat, baseColor: String(svc.status || 'active').toLowerCase() });
    }
    group.add(chipsGroup);
    group.userData.serviceChips = chipsGroup;
    group.userData.serviceLeds = leds;
    return chipsGroup;
  }

  /**
   * Offline appearance kit (SPEC-HK-009 §9.6 graceful degradation): red warning
   * halo ring on the datum plane under the chassis (hidden until offline;
   * scene pulses it), plus per-shell base color/opacity bookkeeping so
   * updateTelemetry can dim/restore smoothly.
   */
  function addOfflineHalo(group, radius, halfH) {
    var T = _T();
    var halo = new T.Mesh(
      new T.TorusGeometry(radius, 0.05, 8, 48),
      new T.MeshStandardMaterial({
        color: color(PALETTE.offlineHalo), emissive: color(PALETTE.offlineHalo),
        emissiveIntensity: 1.2, roughness: 0.4, metalness: 0.0,
        transparent: true, opacity: 0.85
      }));
    halo.rotation.x = -Math.PI / 2;
    halo.position.y = -(halfH || group.userData.halfH || 0.45) + 0.02; // datum plane, local
    halo.visible = false;
    halo.name = 'offline-halo';
    group.add(halo);
    group.userData.halo = halo;
    return halo;
  }

  /** Register all shell materials + geometry bookkeeping on a node group. */
  function finalizeNodeGroup(group, archetype, variant, w, h, d, accentHex) {
    group.userData.archetype = archetype;
    group.userData.variant = variant;
    group.userData.halfH = h / 2;
    group.userData.topY = h / 2;
    group.userData.footprint = { w: w, h: h, d: d };
    group.userData.accentColor = accentHex || PALETTE.accentCyan;
    var shells = [];
    group.traverse(function (o) {
      if (o.isMesh && o.material && o.material.userData && o.material.userData.isShell) {
        shells.push({
          mat: o.material,
          baseColor: o.material.userData.baseColor.clone(),
          baseOpacity: o.material.userData.baseOpacity
        });
      }
    });
    group.userData.shellMaterials = shells;
    group.userData.visual = {
      online: true,
      dieTarget: 0.3,
      vramRatio: 0,
      cpuTarget: 0
    };
    return group;
  }

  // ==========================================================================
  // ARCHETYPE BUILDERS
  // Every builder returns a THREE.Group centered at origin (bottom at -H/2)
  // with userData: {die, slots, vramBar, shellMaterials, halo, topY, ...}.
  // opts: { variant, accentColor, online, services, ... }
  // ==========================================================================

  /**
   * buildMiniPcMesh — universal compact mini-PC squircle (SPEC-HK-009 §3.1):
   * W 2.6 x H 0.9 x D 2.6, corner radius 0.25, frosted translucent shell,
   * top cooling grille (linear fins default / circular mesh for beelink_ser8),
   * motherboard substrate, dual DDR5 SODIMMs, central APU die.
   * Variants: beelink_ser8 (circular top mesh + cyan accent),
   *           beelink_eqi (dual RJ45 surveillance-gateway port cluster).
   */
  function buildMiniPcMesh(opts) {
    opts = opts || {};
    var T = _T();
    var W = 2.6, H = 0.9, D = 2.6, R = 0.25;
    var variant = String(opts.variant || 'mini_pc').toLowerCase();
    var accent = opts.accentColor || (variant === 'beelink_eqi' ? '#f59e0b' : '#22d3ee');
    var g = new T.Group();

    // --- frosted silver-aluminum shell (spec §3.1 mini_pc tuning)
    var shell = new T.Mesh(roundedBoxGeometry(W, H, D, R), createCyberChassisMaterial({
      color: '#8f9bb0', metalness: 0.75, roughness: 0.25,
      transmission: 0.55, opacity: 0.78
    }));
    g.add(shell);

    // --- top cooling grille
    var grilleMat = createMetalMaterial({ color: '#1b2626', roughness: 0.55, metalness: 0.7 });
    var topY = H / 2;
    if (variant === 'beelink_ser8') {
      // circular concentric top mesh (r = 0.8) + cyan accent ring
      [0.24, 0.44, 0.62, 0.8].forEach(function (rr) {
        var ring = new T.Mesh(new T.TorusGeometry(rr, 0.022, 6, 36), grilleMat);
        ring.rotation.x = -Math.PI / 2;
        ring.position.y = topY + 0.006;
        g.add(ring);
      });
      var s8accent = new T.Mesh(new T.TorusGeometry(0.87, 0.018, 6, 40), createGlowMaterial(accent, 0.95));
      s8accent.rotation.x = -Math.PI / 2;
      s8accent.position.y = topY + 0.006;
      g.add(s8accent);
    } else if (variant === 'beelink_eqi') {
      // compact square vent + dual RJ45 front port cluster
      var ventFrame = new T.Mesh(new T.BoxGeometry(1.1, 0.02, 1.1), grilleMat);
      ventFrame.position.y = topY + 0.006;
      g.add(ventFrame);
      for (var v = 0; v < 5; v++) {
        var fin = new T.Mesh(new T.BoxGeometry(1.0, 0.024, 0.06), createMetalMaterial({ color: '#0c1319', roughness: 0.6 }));
        fin.position.set(0, topY + 0.014, -0.4 + v * 0.2);
        g.add(fin);
      }
      var portMat = createMetalMaterial({ color: '#111827', roughness: 0.4 });
      for (var p = 0; p < 2; p++) {
        var rj45 = new T.Mesh(new T.BoxGeometry(0.30, 0.16, 0.10), portMat);
        rj45.position.set(-0.28 + p * 0.30, -H / 2 + 0.28, D / 2 + 0.01);
        g.add(rj45);
        var linkLed = new T.Mesh(new T.SphereGeometry(0.02, 6, 8), createGlowMaterial(p ? '#22d3ee' : '#22c55e', 1.0));
        linkLed.position.set(-0.28 + p * 0.30, -H / 2 + 0.40, D / 2 + 0.02);
        g.add(linkLed);
      }
    } else {
      // universal linear fin grille
      for (var f = 0; f < 7; f++) {
        var finL = new T.Mesh(new T.BoxGeometry(1.5, 0.022, 0.07), grilleMat);
        finL.position.set(0.25, topY + 0.008, -0.7 + f * 0.235);
        g.add(finL);
      }
    }

    // --- interior silicon stack (visible through frosted shell)
    addMotherboardSubstrate(g, W * 0.86, D * 0.86, -H / 2 + 0.06, PALETTE.pcbTrace);
    addRamSodimm(g, -W * 0.28, 0, -H / 2 + 0.20, D * 0.5, '#10b981');
    addRamSodimm(g, W * 0.28, 0, -H / 2 + 0.20, D * 0.5, '#10b981');
    addApuDIE(g, 0.5, -H / 2 + 0.14, PALETTE.dieGlow, variant === 'beelink_eqi' ? 'Intel UHD' : 'Radeon APU');
    addVramBar(g, W * 0.36, D * 0.36, 0.42, 0.16, -H / 2 + 0.05);
    // status LED on front lip
    var fLed = new T.Mesh(new T.SphereGeometry(0.028, 8, 8), createGlowMaterial(accent, 1.0));
    fLed.position.set(W * 0.34, -H / 2 + 0.16, D / 2 + 0.02);
    g.add(fLed);

    addOfflineHalo(g, Math.max(W, D) * 0.62, H / 2);
    return finalizeNodeGroup(g, 'mini_pc', variant, W, H, D, accent);
  }

  /**
   * buildTowerMesh — desktop tower / SFF AI workstation (SPEC-HK-009 §3.1):
   * W 4.2 x H 1.6 x D 3.8 obsidian shell with amber edge rim, smoked glass
   * side window, dual lateral cooling fans, PSU bay, front power cluster,
   * central 1.2x1.2 APU/GPU die, and 12 peripheral memory blocks /
   * inference-slot cubes surrounding the die.
   * Variants: minisforum_ms_s1_max (dual lateral exhaust fin stacks +
   *           live 12-slot APU ring), generic_tower.
   */
  function buildTowerMesh(opts) {
    opts = opts || {};
    var T = _T();
    var W = 4.2, H = 1.6, D = 3.8;
    var variant = String(opts.variant || 'tower').toLowerCase();
    var isMsS1 = variant === 'minisforum_ms_s1_max';
    var accent = opts.accentColor || (isMsS1 ? '#f59e0b' : '#38bdf8');
    var g = new T.Group();

    // --- industrial matte obsidian shell (spec: roughness .18, transmission
    //     .65, opacity .82, amber edge rim)
    var shell = new T.Mesh(roundedBoxGeometry(W, H, D, 0.12), createCyberChassisMaterial({
      color: PALETTE.obsidian, metalness: 0.35, roughness: 0.18,
      transmission: 0.65, opacity: 0.82
    }));
    g.add(shell);
    // amber edge rim wireframe
    var edges = new T.LineSegments(
      new T.EdgesGeometry(shell.geometry, 40),
      new T.LineBasicMaterial({ color: color('#f59e0b'), transparent: true, opacity: 0.55 }));
    g.add(edges);

    // --- smoked glass side window (front face strip)
    var window1 = new T.Mesh(
      new T.BoxGeometry(W * 0.62, H * 0.66, 0.05),
      createSmokedGlassMaterial({ roughness: 0.14 }));
    window1.position.set(W * 0.12, 0, D / 2 + 0.005);
    g.add(window1);

    // --- dual lateral cooling fans (exhaust) + MS-S1 fin stacks
    var fanY = 0.05, fanR = 0.30;
    var fanA = buildFanAssembly(fanR, '#3f4a52');
    fanA.position.set(-W / 2 - 0.02, fanY, -D * 0.18);
    fanA.rotation.y = Math.PI / 2;
    g.add(fanA);
    var fanB = buildFanAssembly(fanR, '#3f4a52');
    fanB.position.set(-W / 2 - 0.02, fanY, D * 0.18);
    fanB.rotation.y = Math.PI / 2;
    g.add(fanB);
    if (isMsS1) {
      var finMat = createMetalMaterial({ color: '#232b2b', roughness: 0.5, metalness: 0.72 });
      // dual lateral exhaust fin stacks flanking the two fans
      [-D * 0.18, D * 0.18].forEach(function (zCenter) {
        for (var fi = 0; fi < 7; fi++) {
          var fin = new T.Mesh(new T.BoxGeometry(0.10, H * 0.55, 0.045), finMat);
          fin.position.set(-W / 2 - 0.05, 0, zCenter + (fi - 3) * 0.085);
          g.add(fin);
        }
      });
    }

    // --- PSU bay (front-left compartment, warm amber standby glow)
    var psu = new T.Mesh(new T.BoxGeometry(W * 0.22, H * 0.72, D * 0.5),
      new T.MeshStandardMaterial({
        color: color('#0c1017'), roughness: 0.6, metalness: 0.35,
        emissive: color('#f59e0b'), emissiveIntensity: 0.10,
        transparent: true, opacity: 0.95
      }));
    psu.position.set(-W * 0.36, 0, -D * 0.2);
    g.add(psu);

    // --- front power cluster (power button + I/O ports)
    var pwrBtn = new T.Mesh(new T.CylinderGeometry(0.07, 0.07, 0.03, 14), createGlowMaterial(accent, 1.05));
    pwrBtn.rotation.x = Math.PI / 2;
    pwrBtn.position.set(W * 0.44, H * 0.28, D / 2 + 0.015);
    g.add(pwrBtn);
    var portMat = createMetalMaterial({ color: '#0d131b', roughness: 0.42 });
    for (var u = 0; u < 3; u++) {
      var usb = new T.Mesh(new T.BoxGeometry(0.14, 0.05, 0.05), portMat);
      usb.position.set(W * 0.30 + u * 0.18, -H * 0.22, D / 2 + 0.01);
      g.add(usb);
    }
    // rear 10GbE copper bay hint
    var rj = new T.Mesh(new T.BoxGeometry(0.30, 0.18, 0.08), portMat);
    rj.position.set(-W * 0.42, 0, -D / 2 - 0.01);
    g.add(rj);

    // --- living silicon: motherboard + giant central die (1.2 x 1.2)
    addMotherboardSubstrate(g, W * 0.8, D * 0.8, -H / 2 + 0.07, isMsS1 ? '#b45309' : PALETTE.pcbTrace);
    addApuDIE(g, 1.2, -H / 2 + 0.16, isMsS1 ? '#ffb84d' : PALETTE.dieGlow, isMsS1 ? 'Strix Halo 8060S' : 'APU/GPU');

    // --- 12-slot array vs static peripheral memory blocks
    if (isMsS1 || opts.slotRing) {
      addSlotRing(g, 12, 1.45, -H / 2 + 0.18, 0.17);
    } else {
      addPeripheralMemoryBlocks(g, 12, W * 0.55, D * 0.5, -H / 2 + 0.07);
      // still expose a 12-entry slot array for telemetry (dim blocks light up)
      groupAsSlotsFromBlocks(g);
    }
    // --- volumetric VRAM pool bar (right interior edge)
    addVramBar(g, W * 0.40, 0, H * 0.78, 0.3, -H / 2 + 0.05);

    addOfflineHalo(g, Math.max(W, D) * 0.58, H / 2);
    return finalizeNodeGroup(g, 'tower', variant, W, H, D, accent);
  }

  /** Bridge static memory blocks to a 12-entry telemetry slot ring (tower). */
  function groupAsSlotsFromBlocks(g) {
    var blocks = g.userData.memoryBlocks || [];
    var slots = [];
    for (var i = 0; i < blocks.length; i++) {
      slots.push({
        mesh: blocks[i], mat: blocks[i].material,
        targetColor: color(PALETTE.slotIdle), targetIntensity: 0.10
      });
    }
    g.userData.slots = slots;
    return slots;
  }

  /**
   * buildPhoneMesh — smartphone slab (SPEC-HK-009 §3.1): W 0.9 x H 0.1 x
   * D 1.8 polished obsidian glass, perimeter metal band, camera visor/island,
   * softly glowing OLED touch surface with sync-pulse indicators.
   * Variant: pixel_9_pro (horizontal full-width camera visor bar + triple lens).
   */
  function buildPhoneMesh(opts) {
    opts = opts || {};
    var T = _T();
    var W = 0.9, H = 0.1, D = 1.8, R = 0.07;
    var variant = String(opts.variant || 'phone').toLowerCase();
    var isPixel = variant === 'pixel_9_pro';
    var accent = opts.accentColor || '#8b5cf6';
    var g = new T.Group();

    // --- polished obsidian glass body (metalness .9, roughness .1, transm .4)
    var shell = new T.Mesh(roundedBoxGeometry(W, H, D, R), createCyberChassisMaterial({
      color: '#0b0e17', metalness: 0.9, roughness: 0.1,
      transmission: 0.4, opacity: 0.86, thickness: 0.4
    }));
    g.add(shell);

    // --- matte metal perimeter band
    var band = new T.Mesh(roundedBoxGeometry(W + 0.035, H * 0.62, D + 0.035, R + 0.01),
      createMetalMaterial({ color: '#4b5563', roughness: 0.32, metalness: 0.9 }));
    g.add(band);

    // --- camera visor / island
    if (isPixel) {
      // horizontal visor bar spanning the full width (spec W .92 H .14 D .3)
      var visor = new T.Mesh(roundedBoxGeometry(0.92, 0.14, 0.3, 0.06),
        createMetalMaterial({ color: '#1f2937', roughness: 0.3, metalness: 0.85 }));
      visor.position.set(0, H / 2 + 0.02, -D * 0.33);
      g.add(visor);
      for (var l = 0; l < 3; l++) {
        var lens = new T.Mesh(new T.CylinderGeometry(0.05, 0.05, 0.03, 16),
          new T.MeshPhysicalMaterial({
            color: color('#050a12'), roughness: 0.05, metalness: 0.2,
            clearcoat: 1.0, clearcoatRoughness: 0.02
          }));
        lens.position.set(-0.24 + l * 0.24, H / 2 + 0.09, -D * 0.33);
        g.add(lens);
      }
    } else {
      var island = new T.Mesh(roundedBoxGeometry(0.45, 0.035, 0.3, 0.08),
        createMetalMaterial({ color: '#182021', roughness: 0.35, metalness: 0.7 }));
      island.position.set(-W * 0.18, H / 2 + 0.016, -D * 0.32);
      g.add(island);
    }

    // --- glowing OLED touch surface
    var oledMat = new T.MeshStandardMaterial({
      color: color('#04121f'), emissive: color('#0ea5e9'), emissiveIntensity: 0.35,
      map: terminalGridTexture(), roughness: 0.2, metalness: 0.0
    });
    var oled = new T.Mesh(new T.PlaneGeometry(W * 0.88, D * 0.92), oledMat);
    oled.rotation.x = -Math.PI / 2;
    oled.position.y = H / 2 + 0.004;
    g.add(oled);
    g.userData.oled = oledMat;

    // --- interior SoC + sync indicator pulse
    addApuDIE(g, 0.34, -H / 2 + 0.02, isPixel ? '#c4b5fd' : PALETTE.dieGlow, isPixel ? 'Tensor G4' : 'SoC');
    var syncPulse = new T.Mesh(new T.SphereGeometry(0.024, 8, 8), createGlowMaterial(accent, 0.9));
    syncPulse.position.set(W * 0.3, H / 2 + 0.02, D * 0.4);
    g.add(syncPulse);
    g.userData.syncPulse = syncPulse;

    addOfflineHalo(g, Math.max(W, D) * 0.62, H / 2);
    return finalizeNodeGroup(g, 'phone', variant, W, H, D, accent);
  }

  /**
   * buildLaptopMesh — clamshell / handheld (SPEC-HK-009 §3.1): base tray
   * W 2.2 x H 0.35 x D 1.6 + hinged display lid W 2.2 x H 1.5 x D 0.1 tilted
   * at 110 deg. Translucent keyboard deck with backlit keys + trackpad;
   * display emits terminal-grid luminescence; internal APU core.
   * Variant: gpd_win_max_2 (split handheld thumbstick accents).
   */
  function buildLaptopMesh(opts) {
    opts = opts || {};
    var T = _T();
    var BW = 2.2, BH = 0.35, BD = 1.6;
    var LW = 2.2, LH = 1.5, LD = 0.1;
    var OPEN_DEG = opts.openDeg !== undefined ? opts.openDeg : 110;
    var variant = String(opts.variant || 'laptop').toLowerCase();
    var isGpd = variant === 'gpd_win_max_2';
    var accent = opts.accentColor || (isGpd ? '#a855f7' : '#38bdf8');
    var g = new T.Group();

    // --- base tray (translucent gunmetal magnesium)
    var base = new T.Mesh(roundedBoxGeometry(BW, BH, BD, 0.07), createCyberChassisMaterial({
      color: '#3d4a4a', metalness: 0.6, roughness: 0.3,
      transmission: 0.5, opacity: 0.8
    }));
    g.add(base);

    // --- backlit keyboard grid
    var keyMat = createGlowMaterial(accent, 0.5);
    var keyBaseMat = createMetalMaterial({ color: '#10181c', roughness: 0.6 });
    var cols = 9, rows = 4;
    var kw = (BW * 0.62) / cols, kd = (BD * 0.52) / rows;
    var kbGroup = new T.Group();
    for (var r = 0; r < rows; r++) {
      for (var c = 0; c < cols; c++) {
        var k = new T.Mesh(new T.BoxGeometry(kw * 0.78, 0.018, kd * 0.72), keyBaseMat);
        k.position.set((c - (cols - 1) / 2) * kw, BH / 2 + 0.01, (r - (rows - 1) / 2) * kd + BD * 0.06);
        kbGroup.add(k);
        var haloK = new T.Mesh(new T.BoxGeometry(kw * 0.86, 0.006, kd * 0.8), keyMat);
        haloK.position.set(k.position.x, BH / 2 + 0.002, k.position.z);
        kbGroup.add(haloK);
      }
    }
    g.add(kbGroup);
    // trackpad
    var pad = new T.Mesh(new T.BoxGeometry(BW * 0.3, 0.014, BD * 0.24),
      createSmokedGlassMaterial({ roughness: 0.06, transmission: 0.65 }));
    pad.position.set(0, BH / 2 + 0.008, BD * 0.3);
    g.add(pad);

    // --- gpd_win_max_2: split handheld thumbstick accents
    if (isGpd) {
      [-1, 1].forEach(function (side) {
        var stem = new T.Mesh(new T.CylinderGeometry(0.055, 0.065, 0.05, 12),
          createMetalMaterial({ color: '#141821', roughness: 0.4 }));
        stem.position.set(side * BW * 0.44, BH / 2 + 0.024, BD * 0.1);
        g.add(stem);
        var cap = new T.Mesh(new T.SphereGeometry(0.055, 10, 8, 0, Math.PI * 2, 0, Math.PI / 2),
          createGlowMaterial(accent, 0.55));
        cap.position.set(side * BW * 0.44, BH / 2 + 0.045, BD * 0.1);
        g.add(cap);
        // shoulder shoulder accent
        var sh = new T.Mesh(new T.BoxGeometry(0.3, 0.04, 0.08), createGlowMaterial(accent, 0.25));
        sh.position.set(side * BW * 0.4, BH / 2 + 0.015, -BD / 2 - 0.02);
        g.add(sh);
      });
    }

    // --- display lid: hinged at rear base edge, tilted back 110 deg
    //     (20 deg past vertical; opening angle from keyboard deck = 110)
    var lidGroup = new T.Group();
    var lidGeo = roundedBoxGeometry(LW, LH, LD, 0.05);
    lidGeo.translate(0, LH / 2, 0); // pivot at bottom edge
    var lid = new T.Mesh(lidGeo, createCyberChassisMaterial({
      color: '#3d4a4a', metalness: 0.6, roughness: 0.3,
      transmission: 0.45, opacity: 0.8
    }));
    lidGroup.add(lid);
    var screenMat = new T.MeshStandardMaterial({
      color: color('#04101c'), emissive: color(accent), emissiveIntensity: 0.3,
      map: terminalGridTexture(), roughness: 0.25, metalness: 0.0
    });
    var screen = new T.Mesh(new T.PlaneGeometry(LW * 0.88, LH * 0.86), screenMat);
    screen.position.set(0, LH / 2, LD / 2 + 0.004);
    lidGroup.add(screen);
    lidGroup.position.set(0, BH / 2, -BD / 2 + 0.03);
    lidGroup.rotation.x = -T.MathUtils.degToRad(OPEN_DEG - 90);
    g.add(lidGroup);
    g.userData.screen = screenMat;

    // --- internal APU core under deck + die glow visible through base
    addMotherboardSubstrate(g, BW * 0.7, BD * 0.6, -BH / 2 + 0.05, PALETTE.pcbTrace);
    addApuDIE(g, 0.42, -BH / 2 + 0.12, PALETTE.dieGlow, 'Radeon 780M');
    addRamSodimm(g, BW * 0.26, -BD * 0.2, -BH / 2 + 0.16, BD * 0.3, '#10b981');

    addOfflineHalo(g, Math.max(BW, BD) * 0.62, BH / 2);
    return finalizeNodeGroup(g, 'laptop', variant, BW, BH + LH * 0.6, BD, accent);
  }

  /**
   * buildTabletMesh — thin digital-paper / touch slate (SPEC-HK-009 §3.1):
   * W 2.0 x H 0.08 x D 2.7, border bezels, vertical edge rail spine,
   * paper-white (e-ink) or slate matte surface.
   * Variant: remarkable_2 (procedural CANVAS paper-grain texture + dark left
   *          spine rail, paper-white finish).
   */
  function buildTabletMesh(opts) {
    opts = opts || {};
    var T = _T();
    var W = 2.0, H = 0.08, D = 2.7, R = 0.035;
    var variant = String(opts.variant || 'tablet').toLowerCase();
    var isRM2 = variant === 'remarkable_2';
    var accent = opts.accentColor || (isRM2 ? '#e2e8f0' : '#10b981');
    var g = new T.Group();

    // --- ultra-thin matte slab (roughness .85, metalness .05 per spec)
    var shellMat = createCyberChassisMaterial({
      color: isRM2 ? '#d8d5cf' : PALETTE.slateScreen,
      metalness: 0.05, roughness: 0.85,
      transmission: 0.18, opacity: 0.94, clearcoat: 0.08
    });
    var shell = new T.Mesh(roundedBoxGeometry(W, H, D, R), shellMat);
    g.add(shell);

    // --- bezel + surface
    var bezel = new T.Mesh(new T.BoxGeometry(W * 0.94, 0.004, D * 0.94),
      createMetalMaterial({ color: isRM2 ? '#b9b5ad' : '#1c1f26', roughness: 0.6 }));
    bezel.position.y = H / 2 + 0.002;
    g.add(bezel);
    var surfaceMat = new T.MeshStandardMaterial({
      color: color(isRM2 ? PALETTE.paperWhite : '#0f172a'),
      emissive: color(isRM2 ? '#334155' : accent),
      emissiveIntensity: isRM2 ? 0.04 : 0.22,
      map: isRM2 ? paperGrainTexture() : null,
      roughness: isRM2 ? 0.95 : 0.35, metalness: 0.0
    });
    var surface = new T.Mesh(new T.PlaneGeometry(W * 0.86, D * 0.88), surfaceMat);
    surface.rotation.x = -Math.PI / 2;
    surface.position.y = H / 2 + 0.005;
    g.add(surface);
    g.userData.screen = surfaceMat;

    // --- perimeter edge rail spine (brushed metal)
    var railMat = createMetalMaterial({ color: isRM2 ? '#1f2426' : '#475569', roughness: 0.3, metalness: 0.9 });
    var rail = new T.Mesh(new T.BoxGeometry(0.045, H * 1.15, D * 0.99), railMat);
    rail.position.set(-W / 2 - 0.01, 0, 0);
    g.add(rail);
    if (isRM2) {
      // remarkable signature: darker left spine rail overshoot + marker nub
      var spine = new T.Mesh(new T.BoxGeometry(0.07, H * 1.2, D * 0.16), railMat);
      spine.position.set(-W / 2 - 0.02, 0, D * 0.42);
      g.add(spine);
      var nub = new T.Mesh(new T.CylinderGeometry(0.045, 0.045, H * 1.35, 12), createGlowMaterial('#64748b', 0.2));
      nub.position.set(-W / 2 - 0.02, 0, D * 0.5);
      g.add(nub);
    } else {
      var pwr = new T.Mesh(new T.BoxGeometry(0.06, 0.016, 0.22), railMat);
      pwr.position.set(W / 2 + 0.012, 0, -D * 0.35);
      g.add(pwr);
    }

    // --- stroke ripple indicator (OCR activity proxy) + faint SoC die
    addApuDIE(g, 0.26, -H / 2 + 0.02, isRM2 ? '#94a3b8' : PALETTE.dieGlow, isRM2 ? 'i.MX6' : 'SoC');
    var ripple = new T.Mesh(new T.TorusGeometry(0.1, 0.008, 6, 24), createGlowMaterial(accent, 0.5));
    ripple.rotation.x = -Math.PI / 2;
    ripple.position.set(W * 0.2, H / 2 + 0.01, D * 0.3);
    g.add(ripple);
    g.userData.ripple = ripple;

    addOfflineHalo(g, Math.max(W, D) * 0.58, H / 2);
    return finalizeNodeGroup(g, 'tablet', variant, W, H, D, accent);
  }

  /**
   * buildServerRackMesh — 1U/2U horizontal rackmount (SPEC-HK-009 §3.1):
   * W 4.8 x H 0.8 x D 4.2 dark brushed steel chassis, front rack-mount ears,
   * hot-swap front drive caddies with LED arrays (green read / amber write),
   * dual redundant rear PSU glow, dual enterprise CPU dies via top window.
   */
  function buildServerRackMesh(opts) {
    opts = opts || {};
    var T = _T();
    var W = 4.8, H = 0.8, D = 4.2;
    var variant = String(opts.variant || 'server_rack').toLowerCase();
    var accent = opts.accentColor || '#38bdf8';
    var g = new T.Group();

    // --- dark brushed steel chassis
    var shell = new T.Mesh(roundedBoxGeometry(W, H, D, 0.035), createCyberChassisMaterial({
      color: '#242c30', metalness: 0.85, roughness: 0.4,
      transmission: 0.12, opacity: 0.96, clearcoat: 0.1
    }));
    g.add(shell);

    // --- front rack-mount ears
    var earMat = createMetalMaterial({ color: '#323b40', roughness: 0.35, metalness: 0.9 });
    [-1, 1].forEach(function (side) {
      var ear = new T.Mesh(new T.BoxGeometry(0.34, H * 0.95, 0.05), earMat);
      ear.position.set(side * (W / 2 + 0.15), 0, D / 2 - 0.01);
      g.add(ear);
      for (var h = 0; h < 2; h++) {
        var hole = new T.Mesh(new T.CylinderGeometry(0.03, 0.03, 0.07, 8),
          createMetalMaterial({ color: '#0b0e13', roughness: 0.6 }));
        hole.rotation.x = Math.PI / 2;
        hole.position.set(side * (W / 2 + 0.15), (h - 0.5) * H * 0.6, D / 2 - 0.01);
        g.add(hole);
      }
    });

    // --- front hot-swap drive caddies + LED arrays
    var caddyMat = createMetalMaterial({ color: '#1a1d21', roughness: 0.45, metalness: 0.75 });
    var nCaddies = 8;
    var cw = (W * 0.88) / nCaddies;
    for (var i = 0; i < nCaddies; i++) {
      var caddy = new T.Mesh(new T.BoxGeometry(cw * 0.86, H * 0.72, 0.08), caddyMat);
      caddy.position.set((i - (nCaddies - 1) / 2) * cw, 0, D / 2 + 0.012);
      g.add(caddy);
      var readLed = new T.Mesh(new T.SphereGeometry(0.016, 6, 6), createGlowMaterial('#22c55e', 0.85));
      readLed.position.set(caddy.position.x - cw * 0.18, H * 0.24, D / 2 + 0.055);
      g.add(readLed);
      var writeLed = new T.Mesh(new T.SphereGeometry(0.016, 6, 6), createGlowMaterial('#f59e0b', 0.45));
      writeLed.position.set(caddy.position.x + cw * 0.18, H * 0.24, D / 2 + 0.055);
      g.add(writeLed);
    }

    // --- dual redundant PSU glow (rear)
    [-1, 1].forEach(function (side) {
      var psu = new T.Mesh(new T.BoxGeometry(0.9, H * 0.6, 0.12),
        new T.MeshStandardMaterial({
          color: color('#0d1117'), roughness: 0.55, metalness: 0.3,
          emissive: color('#f59e0b'), emissiveIntensity: 0.28,
          transparent: true, opacity: 0.95
        }));
      psu.position.set(side * W * 0.34, 0, -D / 2 - 0.02);
      g.add(psu);
    });

    // --- top smoked service window over dual enterprise dies + PCIe riser
    var win = new T.Mesh(new T.BoxGeometry(W * 0.5, 0.03, D * 0.6),
      createSmokedGlassMaterial({ roughness: 0.12 }));
    win.position.set(0, H / 2 + 0.004, 0);
    g.add(win);
    addMotherboardSubstrate(g, W * 0.62, D * 0.68, -H / 2 + 0.05, PALETTE.pcbTrace);
    var dieL = addApuDIE(g, 0.5, -H / 2 + 0.12, accent, 'Xeon 0');
    dieL.position.x = -W * 0.14;
    var dieR = addApuDIE(g, 0.5, -H / 2 + 0.12, accent, 'Xeon 1');
    dieR.position.x = W * 0.14;
    // secondary die registered as the load-modulated one too
    g.userData.die2 = dieR.userData.die;
    // PCIe riser card indicator
    var riser = new T.Mesh(new T.BoxGeometry(0.06, H * 0.5, D * 0.4),
      createMetalMaterial({ color: '#131a1e', roughness: 0.5 }));
    riser.position.set(W * 0.3, 0, -D * 0.1);
    g.add(riser);
    var riserLed = new T.Mesh(new T.BoxGeometry(0.07, 0.02, D * 0.36), createGlowMaterial(accent, 0.6));
    riserLed.position.set(W * 0.3, H * 0.24, -D * 0.1);
    g.add(riserLed);
    addVramBar(g, -W * 0.42, D * 0.3, H * 0.6, 0.2, -H / 2 + 0.04);

    addOfflineHalo(g, Math.max(W, D) * 0.56, H / 2);
    return finalizeNodeGroup(g, 'server_rack', variant, W, H, D, accent);
  }

  // ==========================================================================
  // Datum datum-plane hex grid (SPEC-HK-009 §4.1 mesh-datum-lattice base)
  // ==========================================================================
  function buildHexDatumGrid(size, spacing, colorHex) {
    var T = _T();
    var g = new T.Group();
    size = size || 40; spacing = spacing || 2.0;
    var hexR = spacing * 0.55;
    var pts = [];
    var axMax = Math.ceil(size / (hexR * 1.5)) + 1;
    for (var q = -axMax; q <= axMax; q++) {
      for (var r = -axMax; r <= axMax; r++) {
        var cx = hexR * Math.sqrt(3) * (q + r / 2);
        var cz = hexR * 1.5 * r;
        if (Math.hypot(cx, cz) > size / 2) continue;
        for (var k = 0; k < 6; k++) {
          var a0 = (Math.PI / 180) * (60 * k - 30);
          var a1 = (Math.PI / 180) * (60 * ((k + 1) % 6) - 30);
          pts.push(
            cx + hexR * Math.cos(a0), 0, cz + hexR * Math.sin(a0),
            cx + hexR * Math.cos(a1), 0, cz + hexR * Math.sin(a1));
        }
      }
    }
    var geo = new T.BufferGeometry();
    geo.setAttribute('position', new T.Float32BufferAttribute(pts, 3));
    var gridLines = new T.LineSegments(geo, new T.LineBasicMaterial({
      color: color(colorHex || PALETTE.slateDeep),
      transparent: true, opacity: 0.5
    }));
    gridLines.name = 'datum-hex-grid';
    g.add(gridLines);
    // grounding disc beneath so translucent chassis have a floor
    var disc = new T.Mesh(
      new T.CircleGeometry(size / 2, 48),
      new T.MeshStandardMaterial({
        color: color('#0b1220'), roughness: 1.0, metalness: 0.0,
        transparent: true, opacity: 0.85
      }));
    disc.rotation.x = -Math.PI / 2;
    disc.position.y = -0.006;
    disc.name = 'datum-disc';
    g.add(disc);
    return g;
  }

  // ==========================================================================
  // PHASE 3 (TASK-HK-139): Dynamic 3D Bezier Conduits, HomelabFlowParticle-
  // System, and Tailscale WireGuard Mesh Lattice (SPEC-HK-009 §4, blueprint §4)
  // ==========================================================================

  /**
   * Tiered conduit behavior defaults. Arc heights are deterministically
   * tiered per SPEC-HK-009 §4.2 (inference 2.2 > surveillance 1.8 >
   * ocr_batch 1.6 > syncthing 1.4) so multi-conduit bundles never visually
   * tangle. Payload `arc_height` overrides the tier when present.
   */
  var CONDUIT_DEFAULTS = {
    inference:       { arcHeight: 2.2, color: '#00ffcc', speed: 0.50 },
    syncthing:       { arcHeight: 1.4, color: '#8b5cf6', speed: 0.28 },
    ocr_batch:       { arcHeight: 1.6, color: '#10b981', speed: 0.20 },
    surveillance:    { arcHeight: 1.8, color: '#f59e0b', speed: 0.32 },
    'vision-verify': { arcHeight: 2.0, color: '#ef4444', speed: 0.40 },
    '_default':      { arcHeight: 1.5, color: '#38bdf8', speed: 0.30 }
  };

  /** Single-InstancedMesh photon budget (blueprint §4.1: 256 instances). */
  var FLOW_PARTICLE_BUDGET = 256;

  /** Ping RTT band -> color mapping (mesh-datum-lattice, SPEC-HK-009 §4.1). */
  var PING_BAND_COLORS = {
    good:    PALETTE.pingGood,     // < 15 ms
    warn:    PALETTE.pingWarn,     // 15 - 50 ms
    bad:     PALETTE.pingBad,      // > 50 ms
    unknown: PALETTE.pingUnknown   // no probe data
  };

  /** Tailscale ping RTT band (SPEC-HK-009 §4.1 mesh-datum-lattice). */
  function pingBand(latencyMs) {
    if (latencyMs === null || latencyMs === undefined) return 'unknown';
    var v = Number(latencyMs);
    if (isNaN(v)) return 'unknown';
    if (v < 15) return 'good';
    if (v <= 50) return 'warn';
    return 'bad';
  }

  /**
   * Inference photon color ramp (SPEC-HK-009 §4.1 / blueprint §4.4):
   *   < 30 tok/s           cool electric cyan  #00ffcc
   *   30 - 80 tok/s        vibrant emerald     #10b981
   *   > 80 tok/s           amber -> crimson    #f59e0b ramping to #ef4444
   *                        over 80 -> 140 tok/s
   *   prefill/surge burst  crimson redline     #ef4444
   * Returns a '#rrggbb' string.
   */
  function inferenceColorForToks(toksPerSec, surge) {
    if (surge) return '#ef4444';
    var tok = clamp(toksPerSec, 0, 100000);
    if (tok < 30) return '#00ffcc';
    if (tok <= 80) return '#10b981';
    var k = clamp((tok - 80) / 60, 0, 1);
    var c = color('#f59e0b');
    c.lerp(color('#ef4444'), k);
    return '#' + c.getHexString();
  }

  /** Analytic cubic Bezier tangent dB/dt at parameter t (zero allocation).
   *  Three.js CubicBezierCurve3 stores control points as v0..v3 (spec P0..P3). */
  function bezierTangentAt(curve, t, out) {
    var mt = 1 - t;
    var a = 3 * mt * mt, b = 6 * mt * t, c = 3 * t * t;
    out.set(
      a * (curve.v1.x - curve.v0.x) + b * (curve.v2.x - curve.v1.x) + c * (curve.v3.x - curve.v2.x),
      a * (curve.v1.y - curve.v0.y) + b * (curve.v2.y - curve.v1.y) + c * (curve.v3.y - curve.v2.y),
      a * (curve.v1.z - curve.v0.z) + b * (curve.v2.z - curve.v1.z) + c * (curve.v3.z - curve.v2.z)
    );
    return out;
  }

  /**
   * Collision-free 3D cubic Bezier conduit curve (SPEC-HK-009 §4.2):
   *   P1 = P0 + u_exit  * delta + eY * h_arc   (rises from source chassis)
   *   P2 = P3 + u_entry * delta + eY * h_arc   (descends into target chassis)
   * P0/P3 are machine I/O port anchors (chassis edge, caller-provided).
   * Middle samples t in [3/16 .. 13/16] are checked against obstacleFn(p) ->
   * blocking chassis height; control points lift vertically until clearance
   * holds (endpoints stay pinned at their ports). The returned curve object is
   * live-mutable: layout-mode lerps copy fresh control points into it and the
   * flow particles follow with zero extra bookkeeping.
   * Returns { curve, p0, p1, p2, p3, lift, length, arcHeight }.
   */
  function buildConduitCurve(srcPos, dstPos, arcHeight, opts) {
    opts = opts || {};
    var T = _T();
    var hArc = Math.max(0.2, Number(arcHeight) || 1.5);
    var p0 = new T.Vector3(srcPos.x, srcPos.y + (opts.srcPortY || 0), srcPos.z);
    var p3 = new T.Vector3(dstPos.x, dstPos.y + (opts.dstPortY || 0), dstPos.z);
    var flat = new T.Vector3(p3.x - p0.x, 0, p3.z - p0.z);
    var dist = flat.length();
    if (dist < 1e-6) flat.set(1, 0, 0); else flat.normalize();
    var delta = Math.min(dist * 0.32, 1.4);
    var side = new T.Vector3(-flat.z, 0, flat.x);
    var bow = Number(opts.lateralBow) || 0;
    var p1 = new T.Vector3(p0.x + flat.x * delta + side.x * bow,
                           p0.y + hArc,
                           p0.z + flat.z * delta + side.z * bow);
    var p2 = new T.Vector3(p3.x - flat.x * delta + side.x * bow,
                           p3.y + hArc,
                           p3.z - flat.z * delta + side.z * bow);
    var curve = new T.CubicBezierCurve3(p0, p1, p2, p3);
    // --- chassis clearance pass (mid spans only: ports exit at the edge).
    // Adding L to both control Y values raises B_y(t) by 3t(1-t)*L, so the
    // required lift per sample divides by that influence weight.
    var clearance = opts.clearance !== undefined ? opts.clearance : 0.5;
    var lift = 0;
    if (typeof opts.obstacleFn === 'function') {
      var tmp = new T.Vector3();
      for (var i = 3; i <= 13; i++) {
        var tt = i / 16;
        curve.getPoint(tt, tmp);
        var need = clearance + opts.obstacleFn(tmp);
        if (tmp.y < need) {
          var w = Math.max(0.1, 3 * tt * (1 - tt)); // control-point influence
          lift = Math.max(lift, (need - tmp.y) / w);
        }
      }
      if (lift > 0) { p1.y += lift; p2.y += lift; }
    }
    // --- polyline length (velocity expressed in world units/s -> ds = v dt/L)
    var length = 0, prev = new T.Vector3(), cur = new T.Vector3();
    curve.getPoint(0, prev);
    for (var j = 1; j <= 24; j++) {
      curve.getPoint(j / 24, cur);
      length += cur.distanceTo(prev);
      prev.copy(cur);
    }
    return {
      curve: curve, p0: p0, p1: p1, p2: p2, p3: p3,
      lift: lift, length: Math.max(0.5, length), arcHeight: hArc
    };
  }

  /**
   * Subtle translucent guide tube + brighter emissive core line along a
   * Bezier curve (SPEC-HK-009 §4.1). The group exposes userData.conduit
   * handles for runtime opacity/emissive modulation and geometry rebuilds.
   */
  function buildConduitTube(curve, opts) {
    opts = opts || {};
    var T = _T();
    var baseHex = opts.baseColor || '#38bdf8';
    var g = new T.Group();
    g.name = opts.name || 'conduit';
    var segments = opts.segments || 48;
    var radius = opts.radius || 0.045;
    var tubeMat = new T.MeshStandardMaterial({
      color: color(baseHex), emissive: color(baseHex), emissiveIntensity: 0.35,
      transparent: true, opacity: opts.opacity !== undefined ? opts.opacity : 0.16,
      roughness: 0.4, metalness: 0.05,
      depthWrite: false, side: T.DoubleSide
    });
    var tube = new T.Mesh(new T.TubeGeometry(curve, segments, radius, 6, false), tubeMat);
    tube.name = 'conduit-tube';
    tube.renderOrder = 3;
    g.add(tube);
    var lineMat = new T.LineBasicMaterial({
      color: color(baseHex), transparent: true, opacity: 0.45
    });
    var core = new T.Line(new T.BufferGeometry().setFromPoints(curve.getPoints(segments)), lineMat);
    core.name = 'conduit-core';
    g.add(core);
    g.userData.conduit = {
      tube: tube, tubeMat: tubeMat, core: core, lineMat: lineMat,
      baseColor: baseHex, segments: segments, radius: radius
    };
    return g;
  }

  /** Swap fresh tube/core geometry into a conduit group after control-point
   *  movement (previous geometries are disposed). */
  function rebuildConduitTube(curveGroup, curve) {
    var T = _T();
    var h = curveGroup && curveGroup.userData.conduit;
    if (!h) return curveGroup;
    if (h.tube.geometry) h.tube.geometry.dispose();
    if (h.core.geometry) h.core.geometry.dispose();
    h.tube.geometry = new T.TubeGeometry(curve, h.segments, h.radius, 6, false);
    h.core.geometry = new T.BufferGeometry().setFromPoints(curve.getPoints(h.segments));
    return curveGroup;
  }

  /**
   * Tailscale WireGuard mesh lattice on the datum plane (SPEC-HK-009 §4.1
   * mesh-datum-lattice): subtle glowing interconnect lines joining every
   * active node footprint at Y ~= 0 plus per-node hexagonal rings colored by
   * live ping RTT band (green <15ms, amber 15-50ms, red >50ms, slate = no
   * probe data). nodeRecords: [{ id, x, z, r, h }].
   * Handles live at group.userData.lattice:
   *   { lines, rings, edges, syncPositions(posById), setLatencies(msById),
   *     report(), latencies }
   */
  function buildTailscaleLattice(nodeRecords, opts) {
    opts = opts || {};
    var T = _T();
    var g = new T.Group();
    g.name = 'tailscale-lattice';
    var y = opts.y !== undefined ? opts.y : 0.014;
    var recs = (nodeRecords || []).slice();

    // --- pairwise WireGuard mesh edges (endpoint-band vertex colors)
    var edges = [];
    for (var i = 0; i < recs.length; i++) {
      for (var j = i + 1; j < recs.length; j++) edges.push([i, j]);
    }
    var posArr = new Float32Array(Math.max(1, edges.length) * 6);
    var colArr = new Float32Array(Math.max(1, edges.length) * 6);
    var geo = new T.BufferGeometry();
    geo.setAttribute('position', new T.BufferAttribute(posArr, 3));
    geo.setAttribute('color', new T.BufferAttribute(colArr, 3));
    var lineMat = new T.LineBasicMaterial({
      vertexColors: true, transparent: true,
      opacity: opts.edgeOpacity !== undefined ? opts.edgeOpacity : 0.38
    });
    var lines = new T.LineSegments(geo, lineMat);
    lines.name = 'mesh-lattice-edges';
    lines.renderOrder = 1;
    g.add(lines);

    // --- per-node hexagonal footprint ring (own material = band handle)
    var rings = {};
    recs.forEach(function (nr) {
      var R = Math.max(0.55, Math.min((nr.r || 1) * 1.15, 1.6));
      var pts = [];
      for (var k = 0; k < 6; k++) {
        var a0 = (Math.PI / 3) * k + Math.PI / 6;
        var a1 = (Math.PI / 3) * ((k + 1) % 6) + Math.PI / 6;
        pts.push(new T.Vector3(nr.x + R * Math.cos(a0), y, nr.z + R * Math.sin(a0)));
        pts.push(new T.Vector3(nr.x + R * Math.cos(a1), y, nr.z + R * Math.sin(a1)));
      }
      var rMat = new T.LineBasicMaterial({
        color: color(PING_BAND_COLORS.unknown), transparent: true, opacity: 0.5
      });
      var ring = new T.LineSegments(new T.BufferGeometry().setFromPoints(pts), rMat);
      ring.name = 'mesh-ring:' + nr.id;
      g.add(ring);
      rings[nr.id] = { mesh: ring, mat: rMat, radius: R };
    });

    var latencies = {};     // id -> band string
    var latencyMs = {};     // id -> raw ms (report)
    var lastPos = {};       // id -> { x, z }

    function refreshColors() {
      var ca = new T.Color(), cb = new T.Color();
      for (var e = 0; e < edges.length; e++) {
        ca.set(PING_BAND_COLORS[latencies[recs[edges[e][0]].id] || 'unknown']);
        cb.set(PING_BAND_COLORS[latencies[recs[edges[e][1]].id] || 'unknown']);
        colArr[e * 6 + 0] = ca.r; colArr[e * 6 + 1] = ca.g; colArr[e * 6 + 2] = ca.b;
        colArr[e * 6 + 3] = cb.r; colArr[e * 6 + 4] = cb.g; colArr[e * 6 + 5] = cb.b;
      }
      if (geo.attributes.color) geo.attributes.color.needsUpdate = true;
    }

    var lattice = {
      lines: lines, rings: rings, edges: edges, recs: recs,
      latencies: latencies,
      /** Sync edge vertex positions from (possibly lerped) node coordinates. */
      syncPositions: function (posById) {
        posById = posById || lastPos;
        for (var e = 0; e < edges.length; e++) {
          var a = recs[edges[e][0]], b = recs[edges[e][1]];
          var pa = posById[a.id] || a, pb = posById[b.id] || b;
          lastPos[a.id] = pa; lastPos[b.id] = pb;
          posArr[e * 6 + 0] = pa.x; posArr[e * 6 + 1] = y; posArr[e * 6 + 2] = pa.z;
          posArr[e * 6 + 3] = pb.x; posArr[e * 6 + 4] = y; posArr[e * 6 + 5] = pb.z;
        }
        if (geo.attributes.position) geo.attributes.position.needsUpdate = true;
        refreshColors();
      },
      /** Apply ping RTT values (ms) -> band colors on rings + edge endpoints. */
      setLatencies: function (msById) {
        msById = msById || {};
        Object.keys(rings).forEach(function (id) {
          var ms = msById[id];
          var band = pingBand(ms);
          latencies[id] = band;
          latencyMs[id] = (ms === null || ms === undefined || isNaN(Number(ms))) ? null : Number(ms);
          rings[id].mat.color.set(PING_BAND_COLORS[band]);
          rings[id].mat.opacity = band === 'bad' ? 0.95 : (band === 'unknown' ? 0.5 : 0.85);
        });
        refreshColors();
      },
      report: function () {
        var r = { edges: edges.length, nodes: {} };
        recs.forEach(function (nr) {
          r.nodes[nr.id] = {
            latency_ms: latencyMs[nr.id] !== undefined ? latencyMs[nr.id] : null,
            band: latencies[nr.id] || 'unknown'
          };
        });
        return r;
      }
    };
    g.userData.lattice = lattice;
    return g;
  }

  // --------------------------------------------------------------------------
  // HomelabFlowParticleSystem — single-InstancedMesh photon engine
  // (SPEC-HK-009 §4.2, blueprint §4): parametric progression
  //   s(t+dt) = (s(t) + v*dt / L) mod 1
  // with v in world units/s; on inference_highway v = 0.5 + 0.05 * tok/s.
  // --------------------------------------------------------------------------
  function HomelabFlowParticleSystem(ownerScene, opts) {
    var T = _T();
    opts = opts || {};
    this.options = opts;
    this.owner = ownerScene || null;
    this.time = 0;
    this.speedScale = opts.speedScale !== undefined ? clamp(opts.speedScale, 0.05, 5) : 1;
    this.perConduitBudget = Math.round(clamp(opts.particlesPerConduit || 64, 4, 256));
    this.totalBudget = Math.round(clamp(opts.totalParticles || FLOW_PARTICLE_BUDGET, 4, 4096));
    this.maxSpeed = opts.maxSpeed !== undefined ? Number(opts.maxSpeed) : 16;
    this.ocrPulseInterval = opts.ocrPulseInterval !== undefined ? Number(opts.ocrPulseInterval) : 300;
    this.syncthingBurstInterval = opts.syncthingBurstInterval !== undefined ? Number(opts.syncthingBurstInterval) : 3.6;
    this.conduits = [];
    this.burstsEmitted = 0;
    this.group = new T.Group();
    this.group.name = 'flow-particles';
    this.mesh = null;
    this._dummy = new T.Object3D();
    this._pt = new T.Vector3();
    this._tan = new T.Vector3();
    this._up = new T.Vector3(0, 1, 0);
    this._rngSeed = 12345; // deterministic LCG so headless CI is reproducible
  }

  HomelabFlowParticleSystem.prototype._rng = function () {
    this._rngSeed = (this._rngSeed * 1103515245 + 12345) & 0x7fffffff;
    return this._rngSeed / 0x7fffffff;
  };

  HomelabFlowParticleSystem.prototype.byId = function (conduitId) {
    for (var i = 0; i < this.conduits.length; i++) {
      if (this.conduits[i].id === conduitId) return this.conduits[i];
    }
    return null;
  };

  /**
   * Bind curve records (from HomelabVisualizerScene.buildConduits) into one
   * InstancedMesh. Particle allocation: min(perConduitBudget, floor(total /
   * conduitCount)) each — 4 conduits x 64 = the 256-instance blueprint gate.
   */
  HomelabFlowParticleSystem.prototype.bind = function (records) {
    var T = _T();
    var list = (records || []).filter(function (r) { return r && r.curveRec && r.curveRec.curve; });
    this.dispose();
    this.conduits = [];
    if (!list.length) return this;
    var per = Math.max(4, Math.min(this.perConduitBudget, Math.floor(this.totalBudget / list.length)));
    var total = per * list.length;
    var geo = new T.CapsuleGeometry(0.032, 0.10, 2, 6);
    var mat = new T.MeshBasicMaterial({
      color: 0xffffff, transparent: true, opacity: 0.95,
      blending: T.AdditiveBlending, depthWrite: false
    });
    var mesh = new T.InstancedMesh(geo, mat, total);
    mesh.name = 'flow-photons';
    mesh.frustumCulled = false;
    mesh.renderOrder = 4;
    if (mesh.instanceMatrix && mesh.instanceMatrix.setUsage) {
      mesh.instanceMatrix.setUsage(T.DynamicDrawUsage);
    }
    var zero = new T.Matrix4().makeScale(0, 0, 0);
    var base = 0;
    var self = this;
    list.forEach(function (rec, ci) {
      var def = CONDUIT_DEFAULTS[rec.type] || CONDUIT_DEFAULTS._default;
      var particles = [];
      for (var i = 0; i < per; i++) {
        particles.push({
          s: (i / per + ci * 0.17) % 1,
          jitter: 0.75 + ((i * 37 + ci * 13) % 55) / 100,  // 0.75 .. 1.29
          scale: 0.80 + ((i * 29 + ci * 7) % 45) / 100,    // 0.80 .. 1.24
          lateral: (((i * 53 + ci * 11) % 21) - 10) / 100, // tube-thickness spread
          burst: false, ttl: 0
        });
        mesh.setMatrixAt(base + i, zero);
      }
      self.conduits.push({
        id: rec.id,
        type: rec.type || 'generic',
        def: def,
        curveRec: rec.curveRec,
        curve: rec.curveRec.curve,
        srcId: rec.srcId || null,
        dstId: rec.dstId || null,
        particles: particles,
        baseIndex: base,
        count: per,
        state: {},
        prevActivity: 0,
        speed: def.speed,
        dormant: false,
        color: color(rec.baseColor || def.color),
        currentColor: color(rec.baseColor || def.color),
        targetColor: color(rec.baseColor || def.color),
        burstCursor: 0,
        burstTimer: 1.2 + ci * 0.7,
        respawnOffset: ci * 0.13
      });
      base += per;
    });
    mesh.instanceMatrix.needsUpdate = true;
    this.conduits.forEach(function (c) {
      for (var i = 0; i < c.count; i++) mesh.setColorAt(c.baseIndex + i, c.currentColor);
    });
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    this.mesh = mesh;
    this.group.add(mesh);
    return this;
  };

  /**
   * Ingest one SSE conduits state map (SPEC-HK-009 §6.2). Edge-triggers burst
   * emissions on activity_level rises (vault file changes / Frigate events /
   * OCR batch commits) and on inference surge flags (prompt prefill burst).
   */
  HomelabFlowParticleSystem.prototype.setState = function (conduitState) {
    if (!conduitState || typeof conduitState !== 'object') return;
    var self = this;
    this.conduits.forEach(function (c) {
      var st = conduitState[c.id];
      if (!st || typeof st !== 'object') return;
      var now = clamp(Number(st.activity_level) || 0, 0, 1);
      var surgeNow = st.surge === true;
      var surgeEdge = surgeNow && !c._surgeSeen;
      c._surgeSeen = surgeNow;
      if (c.type === 'inference') {
        if (surgeEdge) self.emitBurst(c.id, { cluster: 16, ttl: 0.9 });
      } else {
        if ((now - c.prevActivity) >= 0.22 || surgeEdge) {
          self.emitBurst(c.id, {
            cluster: c.type === 'surveillance' ? 4 : (c.type === 'ocr_batch' ? 14 : 10),
            ttl: c.type === 'surveillance' ? 0.7 : 1.4
          });
        }
      }
      c.prevActivity = now;
      c.state = st;
    });
  };

  /**
   * Emit a cluster of photon particles bunched along a conduit's curve
   * (Syncthing packet clusters, OCR batch pulses, Frigate event sparkles).
   * Returns the number of particles converted into the cluster.
   */
  HomelabFlowParticleSystem.prototype.emitBurst = function (conduitId, opts) {
    opts = opts || {};
    var c = this.byId(conduitId);
    if (!c) return 0;
    var n = Math.round(clamp(opts.cluster || 10, 1, c.count));
    var ttl = opts.ttl !== undefined ? Number(opts.ttl) : 1.3;
    var start = opts.s !== undefined ? opts.s : this._rng();
    var emitted = 0;
    for (var k = 0; k < n; k++) {
      var p = c.particles[(c.burstCursor + k) % c.count];
      p.s = (start + k * 0.014) % 1;
      p.burst = true;
      p.ttl = ttl * (0.75 + this._rng() * 0.5);
      emitted++;
    }
    c.burstCursor = (c.burstCursor + emitted) % c.count;
    this.burstsEmitted++;
    return emitted;
  };

  /** Global photon speed multiplier (UI particle-speed slider, §7.1). */
  HomelabFlowParticleSystem.prototype.setSpeed = function (scale) {
    this.speedScale = clamp(scale, 0.05, 5);
  };

  /** Advance all photons one frame and write instance matrices/colors. */
  HomelabFlowParticleSystem.prototype.update = function (dt) {
    var T = _T();
    if (!this.mesh || !this.conduits.length) return;
    dt = clamp(dt, 1 / 240, 0.25);
    this.time += dt;
    var k = 1 - Math.pow(0.002, dt);
    var dummy = this._dummy;
    var mesh = this.mesh;
    if (!this._zeroM) this._zeroM = new T.Matrix4().makeScale(0, 0, 0);
    var self = this;

    this.conduits.forEach(function (c) {
      var st = c.state || {};
      var activity = clamp(Number(st.activity_level) || 0, 0, 1);
      var toks = Number(st.tokens_per_sec) || 0;
      var surge = st.surge === true;

      // --- velocity (SPEC §4.1): inference v = 0.5 + 0.05 * tok/s (world u/s)
      var v;
      if (c.type === 'inference') {
        v = 0.5 + 0.05 * toks;
      } else {
        v = c.def.speed * (0.45 + 0.85 * activity);
      }
      // --- graceful degradation: endpoint offline freezes the flow (§9.6)
      var frozen = false;
      if (self.owner && c.srcId && c.dstId) {
        var se = self.owner.nodes.get(c.srcId), de = self.owner.nodes.get(c.dstId);
        if ((se && se.visual && se.visual.online === false) ||
            (de && de.visual && de.visual.online === false)) frozen = true;
      }
      if (frozen) v = 0;
      else v = clamp(v, 0.02, self.maxSpeed) * self.speedScale;
      c.speed = v;

      // --- OCR dormancy: faint dormant wireframe between batch pulses (§4.1)
      c.dormant = (c.type === 'ocr_batch' && activity < 0.25 && toks <= 0 &&
                   !c.particles.some(function (p) { return p.burst; }));

      // --- periodic emitters: syncthing clusters / OCR batch pulses / sparkles
      if (c.type === 'syncthing' && activity > 0.05 && !frozen) {
        c.burstTimer -= dt;
        if (c.burstTimer <= 0) {
          self.emitBurst(c.id, { cluster: 10 });
          c.burstTimer = self.syncthingBurstInterval * (0.85 + self._rng() * 0.4);
        }
      }
      if (c.type === 'ocr_batch' && !frozen) {
        c.burstTimer -= dt;
        if (c.burstTimer <= 0) {
          self.emitBurst(c.id, { cluster: 20, ttl: 1.6 });
          c.burstTimer = self.ocrPulseInterval;
        }
      }
      if (c.type === 'surveillance' && activity > 0.02 && !frozen) {
        c.burstTimer -= dt;
        if (c.burstTimer <= 0) {
          self.emitBurst(c.id, { cluster: 3, ttl: 0.6 }); // event sparkles
          c.burstTimer = 1.6 + self._rng() * 1.8;
        }
      }

      // --- color modulation (blueprint §4.4)
      c.targetColor.set(c.type === 'inference'
        ? inferenceColorForToks(toks, surge)
        : (surge ? '#ef4444' : '#' + c.color.getHexString()));
      c.currentColor.lerp(c.targetColor, k);

      // --- integrate + write instances
      var len = c.curveRec.length || 8;
      var dsBase = v * dt / len;
      for (var i = 0; i < c.count; i++) {
        var p = c.particles[i];
        var idx = c.baseIndex + i;
        if (p.burst) {
          p.ttl -= dt;
          if (p.ttl <= 0) {
            p.burst = false;
            p.s = (i / c.count + c.respawnOffset) % 1; // disperse back to ambient
          }
        }
        if (c.dormant && !p.burst) {
          mesh.setMatrixAt(idx, self._zeroM);
          continue;
        }
        p.s = (p.s + dsBase * (p.burst ? 0.6 : p.jitter)) % 1;
        if (p.s < 0) p.s += 1;
        c.curve.getPoint(p.s, self._pt);
        bezierTangentAt(c.curve, p.s, self._tan);
        if (self._tan.lengthSq() > 1e-8) {
          // lateral spread perpendicular to travel direction (tube volume)
          var ll = Math.hypot(self._tan.z, self._tan.x) || 1;
          dummy.position.set(
            self._pt.x + (-self._tan.z / ll) * p.lateral,
            self._pt.y,
            self._pt.z + (self._tan.x / ll) * p.lateral);
          dummy.quaternion.setFromUnitVectors(self._up, self._tan.normalize());
        } else {
          dummy.position.copy(self._pt);
        }
        var scBase = p.scale * (p.burst ? 1.7 : 1) * (frozen ? 0.35 : 1);
        var elong = 1 + Math.min(v * 0.14, 2.6); // speed streaks elongate photons
        dummy.scale.set(scBase, scBase * elong, scBase);
        dummy.updateMatrix();
        mesh.setMatrixAt(idx, dummy.matrix);
        mesh.setColorAt(idx, c.currentColor);
      }
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  };

  /** HUD/CI report: per-conduit live speed, color, activity, burst state. */
  HomelabFlowParticleSystem.prototype.report = function () {
    var out = {};
    this.conduits.forEach(function (c) {
      var bursts = 0;
      for (var i = 0; i < c.particles.length; i++) if (c.particles[i].burst) bursts++;
      out[c.id] = {
        type: c.type,
        particles: c.count,
        speed: Math.round(c.speed * 1000) / 1000,
        tokens_per_sec: Number((c.state || {}).tokens_per_sec) || 0,
        activity_level: Number((c.state || {}).activity_level) || 0,
        color: '#' + c.currentColor.getHexString(),
        dormant: !!c.dormant,
        burstParticles: bursts
      };
    });
    return out;
  };

  HomelabFlowParticleSystem.prototype.dispose = function () {
    if (this.mesh) {
      if (this.mesh.geometry && this.mesh.geometry.dispose) this.mesh.geometry.dispose();
      if (this.mesh.material && this.mesh.material.dispose) this.mesh.material.dispose();
      if (this.mesh.parent) this.mesh.parent.remove(this.mesh);
      this.mesh = null;
    }
    if (this.group.parent) this.group.parent.remove(this.group);
    this.conduits = [];
  };

  // ==========================================================================
  // HomelabVisualizerScene (SPEC-HK-009 §7, blueprint §1)
  // ==========================================================================
  /**
   * Scene manager: owns the THREE.Scene, camera, renderer, lighting rig, and
   * datum grid; builds node chassis assemblies from the topology contract
   * (GET /api/visualizer/topology) and continuously tweens living-silicon
   * telemetry from the SSE stream (GET /api/visualizer/stream).
   *
   * Headless-safe: construct with { headless: true } (or in a DOM-less
   * environment) to build the full scene graph without a WebGLRenderer —
   * loadTopology/updateTelemetry/step all work for CI verification.
   */
  function HomelabVisualizerScene(container, options) {
    var T = _T();
    options = options || {};
    this.options = options;
    this.THREE = T;

    this.scene = new T.Scene();
    this.scene.background = color(options.background || '#060a12');
    this.scene.fog = new T.Fog(color(options.background || '#060a12'), 30, 90);

    // Camera
    this.camera = new T.PerspectiveCamera(
      options.fov || 50, 16 / 9, 0.1, 250);
    var camPos = options.cameraPosition || [11.5, 8.5, 13.5];
    this.camera.position.set(camPos[0], camPos[1], camPos[2]);
    this.camera.lookAt(new T.Vector3(0, 0.6, 0));

    // Lighting rig (blueprint §1 EnvironmentalRig)
    this.ambient = new T.AmbientLight(color('#1e293b'), 0.6);
    this.scene.add(this.ambient);
    this.keyLight = new T.DirectionalLight(color('#38bdf8'), 1.2);
    this.keyLight.position.set(9, 14, 7);
    this.scene.add(this.keyLight);
    this.rimLight = new T.DirectionalLight(color('#a855f7'), 0.8);
    this.rimLight.position.set(-10, 6, -9);
    this.scene.add(this.rimLight);

    // Datum hex grid (replaced on loadTopology per datum_plane contract)
    this.datumGrid = buildHexDatumGrid(40, 2.0, PALETTE.slateDeep);
    this.scene.add(this.datumGrid);

    // Node chassis groups
    this.nodeGroups = new T.Group();
    this.nodeGroups.name = 'node-chassis';
    this.scene.add(this.nodeGroups);
    this.nodes = new Map();            // id -> { group, visual, slots, ... }
    this.topology = null;
    this.lastConduitState = {};        // phase 3 hand-off buffer

    // Phase 3: Bezier conduits, flow photons, Tailscale lattice (§4)
    this.conduits = new Map();         // conduit id -> live conduit record
    this.conduitsGroup = new T.Group();
    this.conduitsGroup.name = 'conduits';
    this.scene.add(this.conduitsGroup);
    this.flow = null;                  // HomelabFlowParticleSystem
    this.lattice = null;               // buildTailscaleLattice group
    this._conduitSig = '';             // node-position signature (movement)
    this._latSig = '';                 // latency-signature (recolor gate)
    this.layoutMode = 'spatial';

    // Renderer (browser only)
    this.renderer = null;
    this._running = false;
    this._clock = new T.Clock();
    var el = this._resolveContainer(container);
    if (el && !options.headless && typeof T.WebGLRenderer === 'function' &&
        typeof document !== 'undefined') {
      try {
        this.renderer = new T.WebGLRenderer({
          antialias: true, alpha: true, powerPreference: 'high-performance'
        });
        this.renderer.setPixelRatio(Math.min(
          (typeof window !== 'undefined' && window.devicePixelRatio) || 1, 2));
        if (this.renderer.outputColorSpace !== undefined) {
          this.renderer.outputColorSpace = T.SRGBColorSpace;
        }
        if (this.renderer.toneMapping !== undefined) {
          this.renderer.toneMapping = T.ACESFilmicToneMapping;
          this.renderer.toneMappingExposure = 1.1;
        }
        var canvas = this.renderer.domElement;
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.display = 'block';
        el.appendChild(canvas);
        this._container = el;
        this.resize();
        if (typeof ResizeObserver !== 'undefined' && !options.noResizeObserver) {
          var self = this;
          this._resizeObserver = new ResizeObserver(function () { self.resize(); });
          this._resizeObserver.observe(el);
        }
      } catch (err) {
        // SPEC-HK-009 §9.6: no WebGL must never throw out of the scene —
        // degrade to scene-graph-only mode.
        console.warn('[HomelabVisualizer] WebGL unavailable, scene-graph only:', err && err.message);
        this.renderer = null;
      }
    }
  }

  HomelabVisualizerScene.prototype._resolveContainer = function (container) {
    if (!container) {
      if (typeof document !== 'undefined') {
        return document.getElementById('homelabVisualizerCanvas')
          ? document.getElementById('homelabVisualizerCanvas').parentElement
          : document.body;
      }
      return null;
    }
    if (typeof container === 'string') {
      return typeof document !== 'undefined' ? document.querySelector(container) : null;
    }
    return container; // DOM element
  };

  /**
   * loadTopology(topologyData) — build + position all node chassis assemblies
   * from the declarative contract (SPEC-HK-009 §6.1). Idempotent: previous
   * assemblies are disposed and rebuilt. Returns { nodes, conduits,
   * archetypeCounts } for HUD wiring.
   */
  HomelabVisualizerScene.prototype.loadTopology = function (topologyData) {
    var T = _T();
    if (!topologyData || !Array.isArray(topologyData.nodes)) {
      throw new Error('HomelabVisualizer.loadTopology: invalid topology payload (nodes[] required)');
    }
    // clear existing
    var self = this;
    this.nodeGroups.traverse(function (o) {
      if (o.isMesh || o.isLineSegments) {
        if (o.geometry && o.geometry.dispose) o.geometry.dispose();
        if (o.material && o.material.dispose) o.material.dispose();
      }
    });
    this.nodeGroups.clear();
    this.nodes.clear();

    // datum plane contract
    if (topologyData.datum_plane && this.datumGrid) {
      if (this.datumGrid.parent) this.datumGrid.parent.remove(this.datumGrid);
      this.datumGrid = buildHexDatumGrid(
        (topologyData.datum_plane.size && topologyData.datum_plane.size[0]) || 40,
        topologyData.datum_plane.grid_spacing || 2.0,
        topologyData.datum_plane.color || PALETTE.slateDeep);
      this.scene.add(this.datumGrid);
    }

    var archetypeCounts = {};
    topologyData.nodes.forEach(function (nd) {
      var resolved = resolveChassis(nd.chassis_type);
      var builder = {
        mini_pc: buildMiniPcMesh,
        tower: buildTowerMesh,
        phone: buildPhoneMesh,
        laptop: buildLaptopMesh,
        tablet: buildTabletMesh,
        server_rack: buildServerRackMesh
      }[resolved.archetype] || buildMiniPcMesh;

      var group = builder({
        variant: resolved.variant,
        accentColor: undefined,
        node: nd
      });

      var pos = (nd.layout && nd.layout.spatial && nd.layout.spatial.position) || nd.position || [0, 0.45, 0];
      var rot = (nd.layout && nd.layout.spatial && nd.layout.spatial.rotation) || nd.rotation || [0, 0, 0];
      group.position.set(pos[0], pos[1], pos[2]);
      group.rotation.set(rot[0], rot[1], rot[2]);
      group.name = 'node:' + nd.id;

      // docked service chips with health LEDs (SPEC-HK-009 §5.2)
      attachServiceChips(group, nd.services, {
        spreadWidth: Math.max(1.6, (group.userData.footprint.w || 2) * 0.9),
        accentColor: group.userData.accentColor
      });

      // dual-mode layout bookkeeping (blueprint §2 lerp targets)
      group.userData.layoutTargets = {
        spatial: {
          position: new T.Vector3(pos[0], pos[1], pos[2]),
          rotation: rot
        },
        topology: nd.layout && nd.layout.topology
          ? {
              position: new T.Vector3(
                nd.layout.topology.position[0],
                nd.layout.topology.position[1],
                nd.layout.topology.position[2]),
              rotation: nd.layout.topology.rotation || rot
            }
          : null
      };

      self.nodeGroups.add(group);
      self.nodes.set(nd.id, {
        id: nd.id,
        group: group,
        nodeData: nd,
        archetype: resolved.archetype,
        variant: resolved.variant,
        slots: group.userData.slots || [],
        vramBar: group.userData.vramBar || null,
        die: group.userData.die || null,
        die2: group.userData.die2 || null,
        halo: group.userData.halo || null,
        shells: group.userData.shellMaterials || [],
        leds: group.userData.serviceLeds || [],
        visual: group.userData.visual
      });
      archetypeCounts[resolved.archetype] = (archetypeCounts[resolved.archetype] || 0) + 1;
    });

    this.topology = topologyData;
    // Phase 3: build Bezier conduits, flow photons, and datum lattice last so
    // every chassis footprint/port anchor is already positioned (idempotent).
    this.buildConduits(topologyData);
    return {
      nodes: this.nodes.size,
      conduits: (topologyData.conduits || []).length,
      archetypeCounts: archetypeCounts
    };
  };

  /**
   * updateTelemetry(telemetryData) — ingest one SSE frame (SPEC-HK-009 §6.2)
   * and set smooth tween targets for die glow, VRAM fill, slot cubes, service
   * LEDs, and node online/offline appearance. Never throws on partial frames.
   */
  HomelabVisualizerScene.prototype.updateTelemetry = function (telemetryData) {
    if (!telemetryData || typeof telemetryData !== 'object') return;
    var nodesDelta = telemetryData.nodes || {};
    this.lastConduitState = telemetryData.conduits || this.lastConduitState;
    // Phase 3: push live conduit state into the Bezier/flow system (edge
    // triggers Syncthing clusters / OCR pulses / Frigate sparkles / surges).
    this.updateConduits(this.lastConduitState);
    var self = this;

    Object.keys(nodesDelta).forEach(function (id) {
      var entry = self.nodes.get(id);
      if (!entry) return; // node not in current topology render — ignore
      var d = nodesDelta[id] || {};
      var online = d.online !== false;
      var v = entry.visual;

      // --- silicon die illumination: I = 0.2 + 0.8*gpu/100 (+0.1*sin pulse
      //     applied in step()); non-GPU nodes proxy with CPU load.
      var gpu = clamp(d.gpu_busy_pct, 0, 100);
      var cpu = clamp(d.cpu_pct, 0, 100);
      var effLoad = gpu > 0 ? gpu : cpu * 0.6;
      v.online = online;
      // Tailscale ping RTT (mesh-datum-lattice latency indicator, §4.1)
      if (d.latency_ms !== undefined && d.latency_ms !== null && !isNaN(Number(d.latency_ms))) {
        v.latencyMs = Number(d.latency_ms);
      }
      v.dieTarget = online ? 0.2 + 0.8 * (effLoad / 100) : 0.04;
      v.cpuTarget = online ? cpu / 100 : 0;

      // --- volumetric VRAM fill ratio (cyan/amber/redline by threshold)
      var total = Number(d.vram_total_gb) || 0;
      var used = Number(d.vram_used_gb) || 0;
      v.vramRatio = total > 0 ? clamp(used / total, 0, 1) : 0;

      // --- 12 inference slot cubes
      var inf = d.inference || null;
      if (entry.slots.length) {
        var exclusive = !!(inf && ((inf.exclusive_mode === true) ||
          (inf.slots_total > 1 && inf.slots_active >= inf.slots_total && inf.instant_tok_per_sec > 0)));
        var slotList = (inf && Array.isArray(inf.slots)) ? inf.slots : null;
        for (var i = 0; i < entry.slots.length; i++) {
          var slot = entry.slots[i];
          var raw = slotList && slotList[i] ? slotList[i] : null;
          var status = !online ? 'idle'
            : exclusive ? 'exclusive'
            : (raw && raw.status) ? String(raw.status).toLowerCase() : 'idle';
          if (status === 'decode') status = 'decoding';
          var tok = raw ? Number(raw.tok_per_sec) || 0 : 0;
          if (status === 'prefill') {
            slot.targetColor.set(PALETTE.slotPrefill);
            slot.targetIntensity = 0.75;
            slot.pulseRate = 8; // rapid amber pulse
          } else if (status === 'decoding') {
            slot.targetColor.set(PALETTE.slotDecode);
            slot.targetIntensity = 0.5 + clamp(tok / 60, 0, 1) * 0.4;
            slot.pulseRate = 2 + clamp(tok / 30, 0, 1) * 3; // frequency ∝ tok/s
          } else if (status === 'exclusive') {
            slot.targetColor.set(PALETTE.slotExclusive);
            slot.targetIntensity = 0.95;
            slot.pulseRate = 3;
          } else {
            slot.targetColor.set(PALETTE.slotIdle);
            slot.targetIntensity = entry.archetype === 'tower' && !inf ? 0.10 : 0.22;
            slot.pulseRate = 0;
          }
        }
      }

      // --- service chip micro-LEDs: keep declared health colors when online;
      //     node-level offline grays out every LED (SPEC-HK-009 §5.2).
      entry.leds.forEach(function (ledRec) {
        var hex = online
            ? (SERVICE_LED_BY_STATUS[ledRec.baseColor] || PALETTE.ledOffline)
            : '#334155';
        if (!ledRec.targetColor) ledRec.targetColor = new self.THREE.Color();
        ledRec.targetColor.set(hex);
        ledRec.targetIntensity = online ? 1.1 : 0.25;
      });

      // --- offline appearance: dimmed shells + red halo (SPEC-HK-009 §9.6)
      entry.shells.forEach(function (shellRec) {
        shellRec.targetOpacity = online ? shellRec.baseOpacity : Math.min(0.35, shellRec.baseOpacity);
        shellRec.targetColor = shellRec.targetColor || new self.THREE.Color();
        if (!online) shellRec.targetColor.set(0x0a0f1c);
        shellRec.onlineTarget = online;
      });
      if (entry.halo) entry.halo.visible = !online;
    });

    // RAM glow follows memory pressure when present
    Object.keys(nodesDelta).forEach(function (id) {
      var entry = self.nodes.get(id);
      if (!entry) return;
      var d = nodesDelta[id] || {};
      var rt = Number(d.ram_total_gb) || 0;
      if (rt > 0) {
        entry.group.traverse(function (o) {
          if (o.userData && o.userData.ramGlow) {
            o.userData.ramGlowTarget = 0.16 + 0.5 * clamp((Number(d.ram_used_gb) || 0) / rt, 0, 1);
          }
        });
      }
    });
  };

  /** Switch spatial <-> network-topology layout with smooth position lerp. */
  HomelabVisualizerScene.prototype.setLayout = function (mode) {
    if (mode !== 'spatial' && mode !== 'topology') return;
    this.layoutMode = mode;
  };

  // --------------------------------------------------------------------------
  // PHASE 3 scene wiring: buildConduits / updateConduits / step integration
  // --------------------------------------------------------------------------

  /** Elliptical chassis-edge I/O port anchor aimed toward `towardVec`. */
  HomelabVisualizerScene.prototype._portAnchor = function (entry, towardVec) {
    var T = _T();
    var fp = (entry.group.userData && entry.group.userData.footprint) || { w: 2.2, h: 0.9, d: 2.2 };
    var c = entry.group.position;
    var dx = towardVec.x - c.x, dz = towardVec.z - c.z;
    var len = Math.hypot(dx, dz) || 1;
    var ux = dx / len, uz = dz / len;
    var hw = Math.max(fp.w, 0.4) / 2, hd = Math.max(fp.d, 0.4) / 2;
    var r = 1 / Math.hypot(ux / hw, uz / hd);       // ellipse radius along dir
    r = Math.min(r + 0.08, len * 0.45);             // never overshoot halfway
    return new T.Vector3(c.x + ux * r, c.y, c.z + uz * r);
  };

  /** Rounded node-position signature — detects layout-lerp movement cheaply. */
  HomelabVisualizerScene.prototype._conduitSignature = function () {
    if (!this.conduits.size) return '';
    var parts = [];
    this.nodes.forEach(function (entry, id) {
      var p = entry.group.position;
      parts.push(id + ':' + p.x.toFixed(2) + ',' + p.y.toFixed(2) + ',' + p.z.toFixed(2));
    });
    return parts.join('|');
  };

  HomelabVisualizerScene.prototype._latticePosById = function () {
    var out = {};
    this.nodes.forEach(function (entry, id) {
      out[id] = { x: entry.group.position.x, z: entry.group.position.z };
    });
    return out;
  };

  HomelabVisualizerScene.prototype._latencyByNode = function () {
    var out = {};
    this.nodes.forEach(function (entry, id) {
      var ms = entry.visual ? entry.visual.latencyMs : null;
      out[id] = (ms === undefined || ms === null || isNaN(ms)) ? null : ms;
    });
    return out;
  };

  /**
   * buildConduits(topologyData?) — construct collision-free tiered Bezier
   * conduits for topology.conduits (§4.1/§4.2): translucent guide tube +
   * emissive core line per conduit, one HomelabFlowParticleSystem (single
   * 256-instance InstancedMesh) for all photon traffic, and the Tailscale
   * WireGuard mesh lattice on the datum plane. Idempotent: previous conduit
   * assemblies are disposed first. Unknown endpoints warn + skip (§9.6).
   */
  HomelabVisualizerScene.prototype.buildConduits = function (topologyData) {
    var T = _T();
    topologyData = topologyData || this.topology;
    this._disposeConduits();
    var summary = { conduits: 0, flowParticles: 0, latticeEdges: 0 };
    if (!topologyData) return summary;
    var self = this;
    var conduitDefs = Array.isArray(topologyData.conduits) ? topologyData.conduits : [];

    // --- chassis blockers for the collision-free clearance pass
    var blockers = [];
    this.nodes.forEach(function (entry) {
      var fp = (entry.group.userData && entry.group.userData.footprint) || { w: 2, h: 0.9, d: 2 };
      blockers.push({
        x: entry.group.position.x, z: entry.group.position.z,
        r: Math.max(fp.w, fp.d) * 0.55, h: fp.h || 0.9
      });
    });
    function obstacleFn(p) {
      var m = 0;
      for (var i = 0; i < blockers.length; i++) {
        var b = blockers[i];
        if (Math.hypot(p.x - b.x, p.z - b.z) < b.r) m = Math.max(m, b.h);
      }
      return m;
    }
    this._conduitObstacleFn = obstacleFn;

    var records = [];
    conduitDefs.forEach(function (cdef) {
      if (!cdef || !cdef.id) return;
      var src = self.nodes.get(cdef.source_node);
      var dst = self.nodes.get(cdef.target_node);
      if (!src || !dst) {
        if (typeof console !== 'undefined' && console.warn) {
          console.warn('[HomelabVisualizer] conduit endpoints not in topology, skipping:', cdef.id);
        }
        return;
      }
      var def = CONDUIT_DEFAULTS[cdef.type] || CONDUIT_DEFAULTS._default;
      var arcH = Number(cdef.arc_height) || def.arcHeight;
      var a0 = self._portAnchor(src, dst.group.position);
      var a3 = self._portAnchor(dst, src.group.position);
      var curveRec = buildConduitCurve(a0, a3, arcH, { obstacleFn: obstacleFn });
      var baseHex = cdef.base_color || def.color;
      var tubeGroup = buildConduitTube(curveRec.curve, {
        name: 'conduit:' + cdef.id, baseColor: baseHex
      });
      self.conduitsGroup.add(tubeGroup);
      self.conduits.set(cdef.id, {
        id: cdef.id,
        def: def,
        type: cdef.type || 'generic',
        label: cdef.label || cdef.id,
        srcId: cdef.source_node,
        dstId: cdef.target_node,
        curveRec: curveRec,
        tubeGroup: tubeGroup,
        handles: tubeGroup.userData.conduit,
        state: {},
        activity: 0,
        phase: (self.conduits.size * 1.7) % 6.283185,
        anchorSrc: a0,
        anchorDst: a3,
        _lastRebuildAt: 0,
        _rebuilds: 0
      });
      records.push({
        id: cdef.id, type: cdef.type, baseColor: baseHex,
        curveRec: curveRec, srcId: cdef.source_node, dstId: cdef.target_node
      });
    });

    // --- single InstancedMesh photon engine (blueprint §4.1)
    if (records.length) {
      this.flow = new HomelabFlowParticleSystem(this, (this.options && this.options.flow) || {});
      this.flow.bind(records);
      this.conduitsGroup.add(this.flow.group);
      summary.flowParticles = this.flow.mesh ? this.flow.mesh.count : 0;
    }

    // --- Tailscale WireGuard mesh lattice on the datum plane (Y = 0)
    var latRecs = [];
    this.nodes.forEach(function (entry, id) {
      var fp = (entry.group.userData && entry.group.userData.footprint) || { w: 2, h: 0.9, d: 2 };
      latRecs.push({
        id: id, x: entry.group.position.x, z: entry.group.position.z,
        h: fp.h || 0.9, r: Math.max(fp.w, fp.d) / 2
      });
    });
    if (latRecs.length >= 2) {
      this.lattice = buildTailscaleLattice(latRecs, (this.options && this.options.lattice) || {});
      this.scene.add(this.lattice);
      this.lattice.userData.lattice.syncPositions(this._latticePosById());
      this.lattice.userData.lattice.setLatencies(this._latencyByNode());
      summary.latticeEdges = this.lattice.userData.lattice.edges.length;
    }

    summary.conduits = this.conduits.size;
    this._conduitSig = this._conduitSignature();
    return summary;
  };

  /** Ingest the SSE conduits state map (SPEC-HK-009 §6.2). Never throws. */
  HomelabVisualizerScene.prototype.updateConduits = function (conduitState) {
    if (!conduitState || typeof conduitState !== 'object') return;
    var self = this;
    Object.keys(conduitState).forEach(function (id) {
      var c = self.conduits.get(id);
      var st = conduitState[id];
      if (!c || !st || typeof st !== 'object') return;
      c.state = st;
    });
    if (this.flow) this.flow.setState(conduitState);
  };

  /** Public burst trigger (Frigate event hook, vault write hook, HUD). */
  HomelabVisualizerScene.prototype.emitConduitBurst = function (conduitId, opts) {
    return this.flow ? this.flow.emitBurst(conduitId, opts) : 0;
  };

  /** Particle-speed slider wiring (§7.1 visSetSpeed). */
  HomelabVisualizerScene.prototype.setConduitFlowSpeed = function (scale) {
    if (this.flow) this.flow.setSpeed(scale);
  };

  /**
   * Recompute one conduit's Bezier control points from live (possibly
   * layout-lerped) node positions. The curve object is mutated in place so
   * flow particles follow smoothly; tube geometry rebuilds are throttled to
   * ~8 Hz while moving and snap on the settled frame.
   */
  HomelabVisualizerScene.prototype._refreshConduitCurve = function (c) {
    var src = this.nodes.get(c.srcId), dst = this.nodes.get(c.dstId);
    if (!src || !dst || !this._conduitObstacleFn) return;
    var a0 = this._portAnchor(src, dst.group.position);
    var a3 = this._portAnchor(dst, src.group.position);
    var move = c.anchorSrc.distanceToSquared(a0) + c.anchorDst.distanceToSquared(a3);
    var fresh = buildConduitCurve(a0, a3, c.curveRec.arcHeight, { obstacleFn: this._conduitObstacleFn });
    var old = c.curveRec;
    var cpMoved = old.p0.distanceToSquared(fresh.p0) > 4e-4 ||
                  old.p1.distanceToSquared(fresh.p1) > 4e-4 ||
                  old.p2.distanceToSquared(fresh.p2) > 4e-4 ||
                  old.p3.distanceToSquared(fresh.p3) > 4e-4;
    c.anchorSrc.copy(a0);
    c.anchorDst.copy(a3);
    if (!cpMoved) return;
    // mutate in place (curve.v0..v3 === rec.p0..p3 aliases); particles read
    // the control points live, so flow follows layout lerps smoothly
    var cr = old.curve;
    cr.v0.copy(fresh.p0); cr.v1.copy(fresh.p1);
    cr.v2.copy(fresh.p2); cr.v3.copy(fresh.p3);
    old.length = fresh.length;
    old.lift = fresh.lift;
    c._rebuilds++;
    var nowT = this.time || 0;
    var settled = move < 1e-4;
    if (settled || (nowT - (c._lastRebuildAt || 0)) >= 0.12) {
      rebuildConduitTube(c.tubeGroup, cr);
      c._lastRebuildAt = nowT;
    }
  };

  /** Per-frame conduit/flow/lattice advance (called from step()). */
  HomelabVisualizerScene.prototype._stepConduits = function (dt, t, k) {
    var self = this;
    var sig = this._conduitSignature();
    var moved = sig !== this._conduitSig;
    this._conduitSig = sig;
    var posById = moved ? this._latticePosById() : null;

    this.conduits.forEach(function (c) {
      if (moved) self._refreshConduitCurve(c);
      var st = c.state || {};
      var activity = clamp(Number(st.activity_level) || 0, 0, 1);
      c.activity = activity;
      var se = self.nodes.get(c.srcId), de = self.nodes.get(c.dstId);
      var offline = (se && se.visual && se.visual.online === false) ||
                    (de && de.visual && de.visual.online === false);
      var dormant = (c.type === 'ocr_batch' && activity < 0.25 &&
                     !(Number(st.tokens_per_sec) || 0));
      var h = c.handles;
      // guide-tube appearance: dim offline, faint while dormant, glow on activity
      var opTarget = offline ? 0.05 : dormant ? 0.07 : 0.10 + 0.22 * activity;
      h.tubeMat.opacity = lerp(h.tubeMat.opacity, opTarget, k);
      var pulse = 0.5 + 0.5 * Math.sin(2.4 * t + c.phase);
      h.tubeMat.emissiveIntensity = lerp(h.tubeMat.emissiveIntensity,
        offline ? 0.08 : 0.22 + (0.28 + 0.35 * pulse) * (0.25 + 0.75 * activity), k);
      h.lineMat.opacity = lerp(h.lineMat.opacity, offline ? 0.08 : 0.30 + 0.40 * activity, k);
      // high RTT tints the guide tube red (latency is also on the lattice)
      var band = pingBand(st.latency_ms);
      if (band === 'bad') {
        h.tubeMat.emissive.lerp(color(PING_BAND_COLORS.bad), k * 0.5);
      }
    });

    if (this.flow) this.flow.update(dt);

    if (this.lattice && this.lattice.userData.lattice) {
      var lat = this.lattice.userData.lattice;
      if (moved) lat.syncPositions(posById);
      var msById = this._latencyByNode();
      var lsig = JSON.stringify(msById);
      if (lsig !== this._latSig) {
        this._latSig = lsig;
        lat.setLatencies(msById);
      }
    }
  };

  /** Tear down conduit tubes, photons, and lattice (idempotent). */
  HomelabVisualizerScene.prototype._disposeConduits = function () {
    if (this.flow) { this.flow.dispose(); this.flow = null; }
    if (this.lattice) {
      if (this.lattice.parent) this.lattice.parent.remove(this.lattice);
      this.lattice.traverse(function (o) {
        if (o.geometry && o.geometry.dispose) o.geometry.dispose();
        if (o.material && o.material.dispose) o.material.dispose();
      });
      this.lattice = null;
    }
    if (this.conduitsGroup) {
      this.conduitsGroup.traverse(function (o) {
        if (o.geometry && o.geometry.dispose) o.geometry.dispose();
        if (o.material) {
          (Array.isArray(o.material) ? o.material : [o.material]).forEach(function (m) {
            if (m && m.dispose) m.dispose();
          });
        }
      });
      this.conduitsGroup.clear();
    }
    this.conduits.clear();
    this._conduitSig = '';
    this._latSig = '';
  };

  /**
   * step(dt) — advance one animation frame: lerp all tween targets
   * (die glow, VRAM fill, slot cubes, shell dimming, LEDs, RAM glow),
   * spin cooling fans, pulse halos. Public for headless CI; the browser
   * rAF loop calls it automatically.
   */
  HomelabVisualizerScene.prototype.step = function (dt) {
    var T = _T();
    dt = clamp(dt || 1 / 60, 1 / 240, 0.25);
    this.time = (this.time || 0) + dt; // self-advanced clock (headless-safe)
    var t = this.time;
    var k = 1 - Math.pow(0.002, dt); // frame-rate-independent smoothing

    var self = this;
    this.nodes.forEach(function (entry) {
      var v = entry.visual || {};
      var online = v.online !== false;

      // die glow with spec pulse: I = 0.2 + 0.8*load + 0.1*sin(4*pi*t)
      var pulse = online ? 0.1 * Math.sin(4 * Math.PI * t) : 0;
      var targetDie = clamp((v.dieTarget || 0.3) + pulse, 0.02, 1.6);
      if (entry.die) {
        entry.die.emissiveIntensity = lerp(entry.die.emissiveIntensity, targetDie, k);
      }
      if (entry.die2) {
        entry.die2.emissiveIntensity = lerp(entry.die2.emissiveIntensity, targetDie * 0.85, k);
      }

      // VRAM volumetric fill
      if (entry.vramBar) {
        var ratio = clamp(v.vramRatio || 0, 0, 1);
        var cur = entry.vramBar.fill.scale.y;
        entry.vramBar.fill.scale.y = Math.max(0.02, lerp(cur, Math.max(0.02, ratio), k));
        var hex = ratio < 0.70 ? PALETTE.vramLow : (ratio <= 0.90 ? PALETTE.vramMid : PALETTE.vramHigh);
        entry.vramBar.fillMat.emissive.lerp(color(hex), k);
        entry.vramBar.fillMat.color.copy(entry.vramBar.fillMat.emissive);
      }

      // inference slot cubes
      for (var i = 0; i < entry.slots.length; i++) {
        var slot = entry.slots[i];
        var slotPulse = slot.pulseRate ? 0.25 * Math.sin(slot.pulseRate * Math.PI * t) : 0;
        slot.mat.color.lerp(slot.targetColor, k);
        slot.mat.emissive.copy(slot.mat.color);
        slot.mat.emissiveIntensity = lerp(
          slot.mat.emissiveIntensity, clamp(slot.targetIntensity + slotPulse, 0.05, 1.5), k);
      }

      // shell dim / restore
      for (var s = 0; s < entry.shells.length; s++) {
        var rec = entry.shells[s];
        if (rec.targetOpacity !== undefined) {
          rec.mat.opacity = lerp(rec.mat.opacity, rec.targetOpacity, k);
        }
        if (rec.targetColor && rec.targetColor.getHex() !== undefined) {
          if (rec.onlineTarget) {
            rec.mat.color.lerp(rec.baseColor, k);
          } else {
            rec.mat.color.lerp(rec.targetColor, k);
          }
        }
      }

      // service LEDs
      for (var l = 0; l < entry.leds.length; l++) {
        var ledRec = entry.leds[l];
        if (ledRec.targetColor) {
          ledRec.mat.color.lerp(ledRec.targetColor, k);
          ledRec.mat.emissive.copy(ledRec.mat.color);
          if (ledRec.targetIntensity !== undefined) {
            ledRec.mat.emissiveIntensity = lerp(ledRec.mat.emissiveIntensity, ledRec.targetIntensity, k);
          }
        }
      }

      // RAM glow targets
      entry.group.traverse(function (o) {
        if (o.userData && o.userData.ramGlow && o.userData.ramGlowTarget !== undefined) {
          o.userData.ramGlow.emissiveIntensity =
            lerp(o.userData.ramGlow.emissiveIntensity, o.userData.ramGlowTarget, k);
        }
      });

      // offline halo pulse
      if (entry.halo && entry.halo.visible) {
        entry.halo.material.emissiveIntensity = 0.8 + 0.6 * Math.abs(Math.sin(2.2 * Math.PI * t));
        entry.halo.material.opacity = 0.55 + 0.35 * Math.abs(Math.sin(2.2 * Math.PI * t));
      }

      // chassis hover bob for service chip cluster (docking breathing)
      var chips = entry.group.userData.serviceChips;
      if (chips) chips.position.y = Math.sin(t * 1.4 + (entry.id.length || 0)) * 0.015;

      // layout mode lerp (blueprint §2)
      var tgt = entry.group.userData.layoutTargets && entry.group.userData.layoutTargets[self.layoutMode];
      if (tgt && tgt.position) {
        entry.group.position.lerp(tgt.position, k * 0.5);
      }

      // spin fans
      entry.group.traverse(function (o) {
        if (o.userData && o.userData.spin && online) o.rotation.z += dt * 6.5;
      });
    });

    // Phase 3: Bezier conduit recompute (on layout lerp), flow photons,
    // Tailscale lattice sync. Guarded so conduit-less scenes stay pure.
    if (this.conduits && (this.conduits.size || this.lattice)) {
      this._stepConduits(dt, t, k);
    }
  };

  /** Render one frame (no-op renderer-less). */
  HomelabVisualizerScene.prototype.render = function () {
    if (this.renderer) {
      this.renderer.render(this.scene, this.camera);
    }
  };

  /** Start the browser animation loop (renderer required). */
  HomelabVisualizerScene.prototype.start = function () {
    if (this._running) return;
    this._running = true;
    var self = this;
    function loop() {
      if (!self._running) return;
      var dt = self._clock.getDelta();
      self.step(dt);
      self.render();
      if (typeof requestAnimationFrame !== 'undefined') {
        self._raf = requestAnimationFrame(loop);
      }
    }
    // reset clock delta bookkeeping then run
    this._clock.getDelta();
    loop();
  };

  HomelabVisualizerScene.prototype.stop = function () {
    this._running = false;
    if (this._raf && typeof cancelAnimationFrame !== 'undefined') {
      cancelAnimationFrame(this._raf);
    }
  };

  /** Resize camera + renderer to the container's client size. */
  HomelabVisualizerScene.prototype.resize = function () {
    if (!this._container || !this.renderer) return;
    var w = this._container.clientWidth || this._container.width || 1280;
    var h = this._container.clientHeight || this._container.height || 720;
    if (w < 2 || h < 2) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
  };

  /** Return HUD-friendly stats + scene-graph inventory (CI verification). */
  HomelabVisualizerScene.prototype.getSceneStats = function () {
    var meshes = 0, lines = 0, materials = {};
    this.scene.traverse(function (o) {
      if (o.isMesh) meshes++;
      if (o.isLineSegments) lines++;
      if (o.isMesh && o.material && o.material.type) {
        materials[o.material.type] = (materials[o.material.type] || 0) + 1;
      }
    });
    var nodeReport = {};
    this.nodes.forEach(function (entry, id) {
      nodeReport[id] = {
        archetype: entry.archetype,
        variant: entry.variant,
        slots: entry.slots.length,
        vramBar: !!entry.vramBar,
        die: !!entry.die,
        halo: !!entry.halo,
        serviceLeds: entry.leds.length,
        online: entry.visual ? entry.visual.online !== false : true
      };
    });
    return {
      nodes: this.nodes.size,
      meshes: meshes,
      lineSegments: lines,
      materialTypes: materials,
      layoutMode: this.layoutMode,
      renderer: this.renderer ? 'webgl' : 'none(scene-graph only)',
      perNode: nodeReport,
      // Phase 3 conduit inventory
      conduits: this.conduits ? this.conduits.size : 0,
      flowParticles: (this.flow && this.flow.mesh) ? this.flow.mesh.count : 0,
      latticeEdges: (this.lattice && this.lattice.userData.lattice)
        ? this.lattice.userData.lattice.edges.length : 0,
      perConduit: this.flow ? this.flow.report() : {},
      lattice: (this.lattice && this.lattice.userData.lattice)
        ? this.lattice.userData.lattice.report() : null
    };
  };

  /** Full teardown: stop loop, dispose geometries/materials, detach canvas. */
  HomelabVisualizerScene.prototype.dispose = function () {
    this.stop();
    this._disposeConduits();
    if (this._resizeObserver) { try { this._resizeObserver.disconnect(); } catch (e) {} }
    var mats = {};
    this.scene.traverse(function (o) {
      if (o.geometry && o.geometry.dispose) o.geometry.dispose();
      if (o.material) {
        (Array.isArray(o.material) ? o.material : [o.material]).forEach(function (m) {
          if (!mats[m.uuid]) {
            mats[m.uuid] = true;
            if (m.map && m.map.dispose) m.map.dispose();
            if (m.dispose) m.dispose();
          }
        });
      }
    });
    if (this.renderer) {
      this.renderer.dispose();
      if (this.renderer.domElement && this.renderer.domElement.parentElement) {
        this.renderer.domElement.parentElement.removeChild(this.renderer.domElement);
      }
      this.renderer = null;
    }
    this.scene.clear();
    this.nodes.clear();
  };

  // ==========================================================================
  // UMD / window exports
  // ==========================================================================
  var EXPORTS = [
    'HomelabVisualizerScene',
    'buildMiniPcMesh', 'buildTowerMesh', 'buildPhoneMesh',
    'buildLaptopMesh', 'buildTabletMesh', 'buildServerRackMesh',
    'resolveChassis', 'VARIANT_TO_ARCHETYPE', 'VALID_ARCHETYPES', 'PALETTE',
    'createCyberChassisMaterial', 'createSiliconDieMaterial',
    'createMetalMaterial', 'createGlowMaterial', 'createSmokedGlassMaterial',
    'roundedBoxGeometry', 'hexPrismGeometry', 'buildFanAssembly',
    'buildHexDatumGrid', 'terminalGridTexture', 'paperGrainTexture',
    'attachServiceChips', 'addOfflineHalo', 'addSlotRing', 'addVramBar',
    // Phase 3 (TASK-HK-139): conduits, flow photons, mesh lattice
    'HomelabFlowParticleSystem', 'buildConduitCurve', 'buildConduitTube',
    'rebuildConduitTube', 'buildTailscaleLattice', 'bezierTangentAt',
    'inferenceColorForToks', 'pingBand', 'PING_BAND_COLORS',
    'CONDUIT_DEFAULTS', 'FLOW_PARTICLE_BUDGET',
    'setTHREE'
  ];

  var api = {
    HomelabVisualizerScene: HomelabVisualizerScene,
    buildMiniPcMesh: buildMiniPcMesh,
    buildTowerMesh: buildTowerMesh,
    buildPhoneMesh: buildPhoneMesh,
    buildLaptopMesh: buildLaptopMesh,
    buildTabletMesh: buildTabletMesh,
    buildServerRackMesh: buildServerRackMesh,
    resolveChassis: resolveChassis,
    VARIANT_TO_ARCHETYPE: VARIANT_TO_ARCHETYPE,
    VALID_ARCHETYPES: VALID_ARCHETYPES,
    PALETTE: PALETTE,
    createCyberChassisMaterial: createCyberChassisMaterial,
    createSiliconDieMaterial: createSiliconDieMaterial,
    createMetalMaterial: createMetalMaterial,
    createGlowMaterial: createGlowMaterial,
    createSmokedGlassMaterial: createSmokedGlassMaterial,
    roundedBoxGeometry: roundedBoxGeometry,
    hexPrismGeometry: hexPrismGeometry,
    buildFanAssembly: buildFanAssembly,
    buildHexDatumGrid: buildHexDatumGrid,
    terminalGridTexture: terminalGridTexture,
    paperGrainTexture: paperGrainTexture,
    attachServiceChips: attachServiceChips,
    addOfflineHalo: addOfflineHalo,
    addSlotRing: addSlotRing,
    addVramBar: addVramBar,
    // Phase 3 (TASK-HK-139): conduits, flow photons, mesh lattice
    HomelabFlowParticleSystem: HomelabFlowParticleSystem,
    buildConduitCurve: buildConduitCurve,
    buildConduitTube: buildConduitTube,
    rebuildConduitTube: rebuildConduitTube,
    buildTailscaleLattice: buildTailscaleLattice,
    bezierTangentAt: bezierTangentAt,
    inferenceColorForToks: inferenceColorForToks,
    pingBand: pingBand,
    PING_BAND_COLORS: PING_BAND_COLORS,
    CONDUIT_DEFAULTS: CONDUIT_DEFAULTS,
    FLOW_PARTICLE_BUDGET: FLOW_PARTICLE_BUDGET,
    setTHREE: setTHREE,
    EXPORTS: EXPORTS,
    VERSION: '1.1.0-SPEC-HK-009-PHASE3'
  };
  return api;
});
