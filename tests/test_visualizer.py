"""Homelab 3D Spatial Visualizer test suite (SPEC-HK-009 Phase 5 / TASK-HK-141).

Covers:
  1. Topology contract schema via TestClient GET /api/visualizer/topology
     (datum plane, core six nodes, hardware capacities, four conduits,
     zero-IP privacy invariant).
  2. Chassis archetype inference unit tests (phone / tablet / laptop /
     server_rack / tower / mini_pc default / explicit override).
  3. SSE telemetry stream & normalization: the async generator
     `get_telemetry_stream(max_events=N)` is iterated directly (never through
     TestClient, whose SSE reads would block until the generator ends), frame
     format, payload keys, Chunkito's normalized 12-slot ring, graceful
     degradation when the collector raises, and IP sanitization of
     telemetry-derived material.

Hermetic by construction: node_collector network methods are always patched
for the dynamic paths under test — no real fleet traffic is ever emitted.
"""
import asyncio
import json
import re
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api.main import app
from api.services.node_collector import node_collector
from api.services.visualizer_service import (
    CONDUITS,
    DATUM_PLANE,
    VALID_ARCHETYPES,
    VARIANT_TO_ARCHETYPE,
    contains_private_ip,
    infer_chassis_type,
    normalize_inference_slots,
    visualizer_service,
)

CORE_NODE_IDS = {"beehive", "chunkito", "chubbs", "remarkable", "carmel_edge", "pixel_9_pro"}
CONDUIT_IDS = {"inference_highway", "vault_sync_chubbs", "ocr_ingest_remarkable", "carmel_surveillance_neo4j"}

# Spec contract for slot cubes (SPEC-HK-009 §5.1/§6.2): "exclusive" is the
# Woods-Gate whole-GPU mode the frontend understands; the service currently
# emits idle/prefill/decoding.
ALLOWED_SLOT_STATUSES = {"idle", "prefill", "decoding", "exclusive"}

# Independent re-implementation of the privacy tripwire (do NOT reuse the
# service's own regex internals here: the point is a second pair of eyes).
_PRIV_IP_RE = re.compile(
    r"(?<!\d)(?:"
    r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"  # Tailscale CGNAT
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r")(?!\d)"
)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def _hermetic(fn, configured_nodes=None):
    """Run fn() with node_collector.get_configured_nodes pinned (no real
    fleet.yaml / env bleed into the dynamic topology merge)."""
    orig = node_collector.get_configured_nodes
    try:
        node_collector.get_configured_nodes = lambda: list(configured_nodes or [])
        return fn()
    finally:
        node_collector.get_configured_nodes = orig


def _mk_node_telemetry(node_id, status="online", **extra):
    rec = {
        "node_id": node_id,
        "name": node_id,
        "status": status,
        "is_local": node_id == "beehive",
        "latency_ms": 3.2,
        "cpu": {"utilization_percent": 12.5},
        "memory": {"used_gb": 8.0, "total_gb": 32.0},
        "amdgpu": {"gpu_busy_percent": 0.0},
        "apu_vram": {"used_gb": 0.0, "total_gb": 0.0},
    }
    rec.update(extra)
    return rec


def _mk_chunkito_telemetry(raw_slots=None, slots_total=12, loaded_name="qwen3.8-flash-next:262k"):
    """Chunkito telemetry record shaped like NodeTelemetryCollector output:
    3 raw llama-server slots (decoding / prefill / idle) under 12 total."""
    if raw_slots is None:
        raw_slots = [
            # slot 0: decoding — 120 tokens / 3000 ms => 40.0 tok/s
            {"id": 0, "is_processing": True, "is_prompt": False,
             "n_decoded": 120, "predicted_ms": 3000,
             "n_prompt_tokens_processed": 512},
            # slot 1: prefill — direct prompt rate key
            {"id": 1, "is_processing": True, "is_prompt": True,
             "prompt_per_second": 320.5},
            # slot 2: idle
            {"id": 2, "is_processing": False},
        ]
    return _mk_node_telemetry(
        "chunkito",
        cpu={"utilization_percent": 47.0},
        memory={"used_gb": 108.0, "total_gb": 128.0},
        amdgpu={"gpu_busy_percent": 91.0, "temperature_c": 78.0, "power_w": 112.5},
        apu_vram={"used_gb": 94.0, "total_gb": 118.0},
        hardware={"inference_slots": slots_total},
        inference_engine={
            "loaded_models": [{"name": loaded_name}] if loaded_name else [],
            "slots": raw_slots,
        },
    )


async def _ok_collector():
    return [_mk_node_telemetry("beehive"), _mk_chunkito_telemetry()] \
        + [_mk_node_telemetry(n) for n in ("chubbs", "remarkable", "carmel_edge", "pixel_9_pro")]


# ---------------------------------------------------------------------------
# 1. Topology schema & privacy
# ---------------------------------------------------------------------------

class TestVisualizerTopologySchema(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _get(self):
        resp = self.client.get("/api/visualizer/topology")
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_visualizer_topology_schema(self):
        """Full contract check of GET /api/visualizer/topology (SPEC-HK-009 §6.1)."""
        def _run():
            resp = self._get()
            topo = resp.json()

            # -- datum plane -------------------------------------------------
            dp = topo["datum_plane"]
            self.assertEqual(dp["size"], [40, 40])
            self.assertIn("grid_spacing", dp)
            self.assertIsInstance(dp["grid_spacing"], (int, float))
            self.assertIn("color", dp)
            self.assertRegex(str(dp["color"]), r"^#[0-9a-fA-F]{6}$")

            # -- core six nodes ---------------------------------------------
            nodes = {n["id"]: n for n in topo["nodes"]}
            self.assertTrue(CORE_NODE_IDS.issubset(nodes.keys()),
                            f"missing core nodes: {CORE_NODE_IDS - set(nodes)}")
            self.assertEqual(topo["node_count"], len(topo["nodes"]))
            self.assertEqual(topo["conduit_count"], len(topo["conduits"]))

            for nid, node in nodes.items():
                for key in ("id", "name", "role", "chassis_type", "chassis_archetype",
                            "position", "rotation", "layout", "hardware", "services"):
                    self.assertIn(key, node, f"node {nid} missing key {key}")
                self.assertIn(node["chassis_archetype"], VALID_ARCHETYPES)
                self.assertIn("spatial", node["layout"])
                self.assertIn("topology", node["layout"])
                self.assertEqual(len(node["position"]), 3)
                self.assertEqual(len(node["rotation"]), 3)

            # -- Chunkito hardware capacities --------------------------------
            chk = nodes["chunkito"]["hardware"]
            self.assertEqual(chk["inference_slots"], 12)
            self.assertEqual(chk["vram_gb"], 118)
            self.assertIn("8060S", chk["gpu"])
            self.assertEqual(chk["ram_gb"], 128)

            # -- Beehive hardware capacities ---------------------------------
            bee = nodes["beehive"]["hardware"]
            self.assertIn("Ryzen 7 8845HS", bee["cpu"])
            self.assertIn("780M", bee["gpu"])
            self.assertEqual(bee["ram_gb"], 32)

            # -- chassis archetype mapping on live nodes ----------------------
            arch = {nid: nodes[nid]["chassis_archetype"] for nid in CORE_NODE_IDS}
            self.assertEqual(arch["chunkito"], "tower")       # MS-S1 Max APU workstation
            self.assertEqual(arch["pixel_9_pro"], "phone")
            self.assertEqual(arch["chubbs"], "laptop")        # GPD Win Max 2
            self.assertEqual(arch["remarkable"], "tablet")
            self.assertEqual(arch["beehive"], "mini_pc")      # Beelink SER8
            self.assertEqual(arch["carmel_edge"], "mini_pc")  # Beelink EQi

            # -- four conduits -------------------------------------------------
            conduits = {c["id"]: c for c in topo["conduits"]}
            self.assertEqual(set(conduits), CONDUIT_IDS)
            for cid, c in conduits.items():
                for key in ("id", "type", "source_node", "target_node",
                            "label", "arc_height", "base_color"):
                    self.assertIn(key, c, f"conduit {cid} missing key {key}")
                self.assertIn(c["source_node"], nodes)
                self.assertIn(c["target_node"], nodes)
            self.assertEqual(conduits["inference_highway"]["type"], "inference")
            self.assertEqual(conduits["inference_highway"]["source_node"], "beehive")
            self.assertEqual(conduits["inference_highway"]["target_node"], "chunkito")

            self.assertEqual(topo["layout_modes"], ["spatial", "topology"])
            self.assertEqual(topo["spec"], "SPEC-HK-009")
            return topo

        topo = _hermetic(_run)
        # zero-IP invariant also on the schema test's own response
        self.assertFalse(contains_private_ip(topo))

    def test_topology_zero_ip_privacy_invariant(self):
        """No Tailscale (100.64.0.0/10), RFC1918, or loopback IP material may
        appear anywhere in the topology response (blueprint §5.1)."""
        def _run():
            resp = self._get()
            raw_text = resp.text
            topo = resp.json()

            # tripwire on serialized text and parsed tree
            self.assertIsNone(_PRIV_IP_RE.search(raw_text),
                              "private IP leaked in topology response text")
            self.assertFalse(contains_private_ip(topo))

            # address-bearing keys can never appear in visualizer payloads
            banned_keys = {"host", "hostname", "tailscale_ip", "tailscale",
                           "ip", "ipv4", "ipv6", "ip_address", "address",
                           "addresses", "ssh_target", "mac"}

            def _walk(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        self.assertNotIn(str(k).lower(), banned_keys,
                                         f"forbidden key '{k}' in topology payload")
                        _walk(v)
                elif isinstance(obj, list):
                    for item in obj:
                        _walk(item)

            _walk(topo)

        _hermetic(_run)

    def test_topology_dynamic_node_merge_and_sanitization(self):
        """Extra SPEC-HK-008 fleet nodes merge in with inferred archetypes and
        sanitized hardware metadata (no host/IP keys, no IP strings)."""
        extra = {
            "id": "waywise_pine",
            "name": "Waywise Pine",
            "role": "storage_worker",
            "tags": ["tower", "desktop"],
            "hardware": {
                "cpu": "Intel Xeon E-2224G",
                "ram_gb": 64,
                "host": "waywise-pine.tailnet.ts.net",      # must be stripped
                "tailscale_ip": "100.101.11.12",            # must be stripped
                "note": "reachable at 192.168.50.7",        # IP must be redacted
            },
        }

        def _run():
            resp = self._get()
            topo = resp.json()
            nodes = {n["id"]: n for n in topo["nodes"]}
            self.assertIn("waywise_pine", nodes)
            node = nodes["waywise_pine"]
            self.assertEqual(node["chassis_archetype"], "tower")
            self.assertNotIn("host", node["hardware"])
            self.assertNotIn("tailscale_ip", node["hardware"])
            self.assertEqual(node["hardware"].get("note"), "reachable at [redacted]")
            self.assertIsNone(_PRIV_IP_RE.search(json.dumps(topo)))
            self.assertEqual(topo["node_count"], len(topo["nodes"]))
            return topo

        _hermetic(_run, configured_nodes=[extra])


# ---------------------------------------------------------------------------
# 2. Chassis type inference
# ---------------------------------------------------------------------------

class TestChassisInference(unittest.TestCase):

    def test_infer_chassis_type(self):
        infer = visualizer_service.infer_chassis_type  # exercises the method facade
        self.assertEqual(infer({"tags": ["phone", "android"]}),
                         infer_chassis_type({"tags": ["phone", "android"]}))

        # -- phone -------------------------------------------------------------
        self.assertEqual(infer_chassis_type({"tags": ["phone", "android"]}), "phone")
        self.assertEqual(infer_chassis_type({"tags": ["Pixel", "mobile"]}), "phone")
        self.assertEqual(infer_chassis_type({"tags": ["iPhone", "ios"]}), "phone")

        # -- tablet --------------------------------------------------------------
        self.assertEqual(infer_chassis_type({"tags": ["tablet"]}), "tablet")
        self.assertEqual(infer_chassis_type({"tags": ["reMarkable", "e-ink"]}), "tablet")

        # -- laptop --------------------------------------------------------------
        self.assertEqual(infer_chassis_type({"tags": ["handheld"]}), "laptop")
        self.assertEqual(infer_chassis_type({"tags": ["bazzite", "clamshell"]}), "laptop")
        self.assertEqual(infer_chassis_type({"tags": ["macbook"]}), "laptop")

        # -- server_rack ----------------------------------------------------------
        self.assertEqual(infer_chassis_type({"tags": ["2u", "supermicro"]}), "server_rack")
        self.assertEqual(infer_chassis_type({"tags": ["rack"]}), "server_rack")
        self.assertEqual(infer_chassis_type({"tags": ["poweredge"]}), "server_rack")

        # -- tower: high VRAM / Strix Halo 8060S / heavy role ---------------------
        self.assertEqual(infer_chassis_type({"hardware": {"vram_gb": 64}}), "tower")
        self.assertEqual(infer_chassis_type({"hardware": {"vram_gb": 118, "ram_gb": 128}}), "tower")
        self.assertEqual(
            infer_chassis_type({"hardware": {"gpu": "AMD Radeon 8060S (gfx1151, 40 CUs)"}}), "tower")
        self.assertEqual(infer_chassis_type({"hardware": {"gpu": "NVIDIA RTX 4090"}}), "tower")
        self.assertEqual(infer_chassis_type({"tags": ["heavy_inference"]}), "tower")
        self.assertEqual(infer_chassis_type({"tags": ["strix-halo"]}), "tower")
        # role-only inference (as used by the dynamic topology merge)
        self.assertEqual(infer_chassis_type({"tags": [], "hardware": {"vram_gb": 96},
                                             "role": "inference_worker"}), "tower")

        # -- default mini_pc --------------------------------------------------------
        self.assertEqual(infer_chassis_type({}), "mini_pc")
        self.assertEqual(infer_chassis_type({"tags": ["homelab"], "role": "worker",
                                             "hardware": {"vram_gb": 2}}), "mini_pc")

        # -- explicit override always wins -------------------------------------------
        self.assertEqual(infer_chassis_type({"chassis_type": "server_rack",
                                             "tags": ["phone", "tablet"]}), "server_rack")
        self.assertEqual(infer_chassis_type({"chassis_type": "mini_pc",
                                             "hardware": {"vram_gb": 118}}), "mini_pc")
        self.assertEqual(infer_chassis_type({"chassis_type": "tower"}), "tower")

        # -- every inference result is a valid archetype --------------------------------
        for node in ({}, {"tags": ["pixel"]}, {"tags": ["1u"]}, {"hardware": {"gpu": "h100"}}):
            self.assertIn(infer_chassis_type(node), VALID_ARCHETYPES)

    def test_variant_to_archetype_preset_map(self):
        """SPEC-HK-009 §3.3 preset variants resolve to their parent archetype."""
        expected = {
            "beelink_ser8": "mini_pc", "beelink_eqi": "mini_pc",
            "minisforum_ms_s1_max": "tower", "generic_tower": "tower",
            "gpd_win_max_2": "laptop", "generic_laptop": "laptop",
            "remarkable_2": "tablet", "generic_tablet": "tablet",
            "pixel_9_pro": "phone", "generic_phone": "phone",
        }
        self.assertEqual(VARIANT_TO_ARCHETYPE, expected)
        for arch in VARIANT_TO_ARCHETYPE.values():
            self.assertIn(arch, VALID_ARCHETYPES)


# ---------------------------------------------------------------------------
# 3. Slot normalization units
# ---------------------------------------------------------------------------

class TestSlotNormalization(unittest.TestCase):

    def test_normalize_pads_to_twelve_slots(self):
        raw = [{"id": 0, "is_processing": True, "n_decoded": 120, "predicted_ms": 3000}]
        slots = normalize_inference_slots(raw, slots_total=12)
        self.assertEqual(len(slots), 12)
        self.assertEqual([s["slot_id"] for s in slots], list(range(12)))
        for s in slots:
            self.assertIn(s["status"], ALLOWED_SLOT_STATUSES)
            self.assertIsInstance(s["tok_per_sec"], float)
            self.assertIn("model", s)
        self.assertEqual(slots[0]["status"], "decoding")
        self.assertAlmostEqual(slots[0]["tok_per_sec"], 40.0, places=1)  # 120 / 3.0s
        for s in slots[1:]:
            self.assertEqual(s["status"], "idle")
            self.assertEqual(s["tok_per_sec"], 0.0)
            self.assertIsNone(s["model"])

    def test_normalize_truncates_overflow_beyond_twelve(self):
        raw = [{"id": i, "is_processing": False} for i in range(20)]
        self.assertEqual(len(normalize_inference_slots(raw, slots_total=12)), 12)

    def test_status_mapping(self):
        raw = [
            {"is_processing": False},                                             # idle
            {"is_processing": True, "is_prompt": True, "prompt_per_second": 100},  # prefill
            {"is_processing": True, "n_prompt_tokens_processed": 64, "n_decoded": 0},  # prefill
            {"is_processing": True, "n_prompt_tokens_processed": 64, "n_decoded": 9},  # decoding
        ]
        slots = normalize_inference_slots(raw, slots_total=12)
        self.assertEqual([s["status"] for s in slots[:4]],
                         ["idle", "prefill", "prefill", "decoding"])

    def test_tok_per_sec_extraction_strategies(self):
        raw = [
            {"is_processing": True, "n_decoded": 5, "decode_per_second": 44.3},       # direct key
            {"is_processing": True, "is_prompt": True, "prefill_tok_per_sec": 300.0},  # prefill key
            {"is_processing": True, "n_decoded": 10, "mean_duration_per_token": 25.0}, # 1000/25 = 40
            {"is_processing": True},                                                    # no data -> 0
        ]
        slots = normalize_inference_slots(raw, slots_total=12)
        self.assertAlmostEqual(slots[0]["tok_per_sec"], 44.3, places=1)
        self.assertAlmostEqual(slots[1]["tok_per_sec"], 300.0, places=1)
        self.assertAlmostEqual(slots[2]["tok_per_sec"], 40.0, places=1)
        self.assertEqual(slots[3]["tok_per_sec"], 0.0)

    def test_resident_model_and_persona(self):
        raw = [
            {"is_processing": True, "n_decoded": 30, "predicted_ms": 1000,
             "persona": "jagular"},
            {"is_processing": True, "n_decoded": 30, "predicted_ms": 1000,
             "model": "explicit-model:7b"},
        ]
        slots = normalize_inference_slots(raw, slots_total=12, resident_model="qwen:resident")
        self.assertEqual(slots[0]["model"], "qwen:resident")   # resident fallback while active
        self.assertEqual(slots[0]["persona"], "jagular")
        self.assertEqual(slots[1]["model"], "explicit-model:7b")  # explicit wins


# ---------------------------------------------------------------------------
# 4. SSE telemetry stream (direct async-generator iteration)
# ---------------------------------------------------------------------------

class TestTelemetryStream(unittest.TestCase):
    """Iterate get_telemetry_stream() directly — TestClient SSE reads hang on
    the never-ending production generator, so max_events bounds the drain."""

    def _drain(self, collector, max_events=2):
        async def _collect():
            frames = []
            async for frame in visualizer_service.get_telemetry_stream(
                    interval=0.01, max_events=max_events):
                frames.append(frame)
            return frames

        with mock.patch.object(node_collector, "get_all_nodes_telemetry",
                               new=mock.AsyncMock(side_effect=collector)):
            return asyncio.run(_collect())

    def test_stream_frame_format_and_payload_keys(self):
        """Each frame is `data: <json>\\n\\n` and parses with the payload keys
        timestamp / nodes / conduits (SPEC-HK-009 §6.2)."""
        frames = self._drain(_ok_collector, max_events=2)
        self.assertEqual(len(frames), 2)

        for frame in frames:
            self.assertIsInstance(frame, str)
            self.assertTrue(frame.startswith("data: "), f"bad SSE prefix: {frame[:24]!r}")
            self.assertTrue(frame.endswith("\n\n"))
            payload = json.loads(frame[len("data: "):])  # must not raise
            for key in ("timestamp", "nodes", "conduits"):
                self.assertIn(key, payload)
            self.assertIsInstance(payload["timestamp"], (int, float))

            # core six degrade-free present; chunkito carries inference block
            self.assertTrue(CORE_NODE_IDS.issubset(payload["nodes"].keys()))
            for nid, delta in payload["nodes"].items():
                for key in ("online", "cpu_pct", "gpu_busy_pct", "temperature_c",
                            "power_w", "vram_used_gb", "vram_total_gb",
                            "ram_used_gb", "ram_total_gb", "latency_ms"):
                    self.assertIn(key, delta, f"node {nid} missing delta key {key}")
            self.assertIn("inference", payload["nodes"]["chunkito"])

            # conduits mirrored with activity state
            self.assertEqual(set(payload["conduits"]), CONDUIT_IDS)
            for cid, entry in payload["conduits"].items():
                self.assertIn("active", entry)
                self.assertIn("activity_level", entry)
            self.assertIn("tokens_per_sec", payload["conduits"]["inference_highway"])
            self.assertNotIn("collector_error", payload)

        # live values from the ok collector surface through
        payload = json.loads(frames[-1][len("data: "):])
        chk = payload["nodes"]["chunkito"]
        self.assertTrue(chk["online"])
        self.assertAlmostEqual(chk["gpu_busy_pct"], 91.0, places=1)
        self.assertAlmostEqual(chk["vram_total_gb"], 118.0, places=1)
        self.assertAlmostEqual(chk["inference"]["instant_tok_per_sec"], 360.5, places=1)
        self.assertTrue(payload["conduits"]["inference_highway"]["active"])

    def test_stream_chunkito_twelve_slot_normalization(self):
        """Stream ring: exactly 12 slots with status/tok_per_sec/model, raw
        llama-server slots padded, resident model propagated."""
        frames = self._drain(_ok_collector, max_events=1)
        payload = json.loads(frames[0][len("data: "):])
        inf = payload["nodes"]["chunkito"]["inference"]

        self.assertEqual(inf["slots_total"], 12)
        self.assertEqual(len(inf["slots"]), 12)
        self.assertEqual(inf["resident_model"], "qwen3.8-flash-next:262k")
        self.assertEqual(inf["slots_active"], 2)   # decoding + prefill
        self.assertTrue(inf["prefill_burst"])      # one slot in prefill

        for slot in inf["slots"]:
            self.assertIn(slot["status"], ALLOWED_SLOT_STATUSES)
            self.assertIsInstance(slot["tok_per_sec"], float)
            self.assertIn("model", slot)
            self.assertIsInstance(slot["slot_id"], int)

        self.assertEqual(inf["slots"][0]["status"], "decoding")
        self.assertAlmostEqual(inf["slots"][0]["tok_per_sec"], 40.0, places=1)
        self.assertEqual(inf["slots"][1]["status"], "prefill")
        self.assertAlmostEqual(inf["slots"][1]["tok_per_sec"], 320.5, places=1)
        for slot in inf["slots"][2:]:
            self.assertEqual(slot["status"], "idle")

    def test_stream_graceful_degradation_on_collector_error(self):
        """Collector raising must yield a degraded frame tagged
        collector_error — never crash the stream."""
        boom = RuntimeError("tailscale down: connection refused to 100.99.1.5:9100")

        async def _failing_collector():
            raise boom

        frames = self._drain(_failing_collector, max_events=2)
        self.assertEqual(len(frames), 2)  # stream survives, keeps emitting

        for frame in frames:
            self.assertTrue(frame.startswith("data: "))
            payload = json.loads(frame[len("data: "):])
            self.assertIn("collector_error", payload)
            # all core nodes degrade to offline deltas
            self.assertTrue(CORE_NODE_IDS.issubset(payload["nodes"].keys()))
            for nid, delta in payload["nodes"].items():
                self.assertFalse(delta["online"], f"{nid} should be offline-degraded")
            # inference ring still well-formed and zeroed while offline
            inf = payload["nodes"]["chunkito"]["inference"]
            self.assertEqual(len(inf["slots"]), 12)
            self.assertEqual(inf["slots_active"], 0)
            self.assertEqual(inf["instant_tok_per_sec"], 0.0)
            # no conduit may claim activity with dead endpoints
            for entry in payload["conduits"].values():
                self.assertFalse(entry["active"])
            # error text IP material is redacted, never leaked
            self.assertFalse(contains_private_ip(payload))
            self.assertNotIn("100.99.1.5", json.dumps(payload))

    def test_stream_unreachable_nodes_degrade_individually(self):
        """Partial telemetry (only beehive + chunkito reachable): the other
        four core nodes go offline and their conduits deactivate, while the
        inference highway stays live."""
        async def _partial_collector():
            return [_mk_node_telemetry("beehive"), _mk_chunkito_telemetry()]

        frames = self._drain(_partial_collector, max_events=1)
        payload = json.loads(frames[0][len("data: "):])

        self.assertTrue(payload["nodes"]["beehive"]["online"])
        self.assertTrue(payload["nodes"]["chunkito"]["online"])
        for nid in ("chubbs", "remarkable", "carmel_edge", "pixel_9_pro"):
            self.assertFalse(payload["nodes"][nid]["online"])
            self.assertEqual(payload["nodes"][nid]["vram_used_gb"], 0.0)
            self.assertIsNone(payload["nodes"][nid]["temperature_c"])

        self.assertTrue(payload["conduits"]["inference_highway"]["active"])
        self.assertFalse(payload["conduits"]["vault_sync_chubbs"]["active"])
        self.assertFalse(payload["conduits"]["ocr_ingest_remarkable"]["active"])
        self.assertFalse(payload["conduits"]["carmel_surveillance_neo4j"]["active"])

    def test_stream_sanitizes_telemetry_ip_material(self):
        """Telemetry carrying host/IP-bearing keys and IP strings is stripped
        or redacted before the frame leaves the service (blueprint §5.1)."""
        async def _leaky_collector():
            rec = _mk_node_telemetry(
                "beehive",
                tailscale_ip="100.64.45.77",
                hostname="beehive.tailnet.ts.net",
                name="beehive at 10.20.30.3",
                error_detail="peer 172.16.9.9 unreachable; alt 192.168.4.4 down",
            )
            return [rec] + [_mk_node_telemetry(n) for n in
                            ("chunkito", "chubbs", "remarkable", "carmel_edge", "pixel_9_pro")]

        frames = self._drain(_leaky_collector, max_events=1)
        payload = json.loads(frames[0][len("data: "):])
        serialized = json.dumps(payload)

        self.assertIsNone(_PRIV_IP_RE.search(serialized))
        self.assertFalse(contains_private_ip(payload))
        self.assertNotIn("100.64.45.77", serialized)
        self.assertNotIn("10.20.30.3", serialized)
        self.assertNotIn("tailscale_ip", serialized)
        self.assertNotIn("hostname", serialized)

    def test_stream_route_registered(self):
        """The SSE route exists on the app without issuing a streaming request
        (TestClient would block on the unbounded generator). Checked via the
        OpenAPI schema because this FastAPI build mounts included routers as
        lazy _IncludedRouter wrappers."""
        paths = app.openapi()["paths"]
        self.assertIn("/api/visualizer/topology", paths)
        self.assertIn("/api/visualizer/stream", paths)
        self.assertIn("get", paths["/api/visualizer/stream"])
        params = {p["name"] for p in paths["/api/visualizer/stream"]["get"].get("parameters", [])}
        self.assertIn("interval", params)


if __name__ == "__main__":
    unittest.main()
