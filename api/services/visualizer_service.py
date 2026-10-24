"""Homelab 3D Spatial Visualizer service (SPEC-HK-009, TASK-HK-137).

Maintains the declarative homelab topology registry (spatial + network-topology
dual-mode coordinates, procedural chassis archetypes, docked services, and
conduit definitions), and emits a 1Hz Server-Sent Events telemetry stream
derived from the live NodeTelemetryCollector.

SECURITY INVARIANTS (SPEC-HK-009 / architecture blueprint §5):
  * Zero raw private Tailscale (100.x.y.z) or LAN IPs ever leave this service.
    All payloads pass through `_sanitize_payload()` before serialization and
    the topology registry is a static declarative model keyed only by logical
    node ID and role.
  * Strictly read-only: no mutative fleet controls live here.
"""
import asyncio
import copy
import json
import logging
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from api.services.node_collector import node_collector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Privacy sanitization (architecture blueprint §5.1)
# ---------------------------------------------------------------------------

# RFC1918 + Tailscale CGNAT (100.64.0.0/10) + loopback patterns appearing in
# any string value are redacted before serialization.
_PRIVATE_IP_RE = re.compile(
    r"(?<!\d)(?:"
    r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r")(?!\d)"
)

# Keys that can never appear in visualizer payloads, regardless of value.
_SENSITIVE_KEYS = {
    "host", "hostname", "hostname_aliases", "tailscale_ip", "tailscale",
    "ip", "ipv4", "ipv6", "ip_address", "address", "addresses",
    "endpoint", "endpoint_url", "port", "ssh_target", "mac",
}

# NOTE: "port" is stripped only from *telemetry-derived* structures; the
# static topology registry intentionally declares docked service ports
# (SPEC-HK-009 §6.1), which are public knowledge of the open-source layout.


def _sanitize_payload(obj: Any, keep_service_ports: bool = False) -> Any:
    """Recursively strip sensitive host/IP material from a payload tree."""
    if isinstance(obj, dict):
        sanitized: Dict[str, Any] = {}
        for key, value in obj.items():
            key_l = str(key).lower()
            if key_l in _SENSITIVE_KEYS:
                if keep_service_ports and key_l == "port":
                    sanitized[key] = value
                continue
            sanitized[key] = _sanitize_payload(value, keep_service_ports=keep_service_ports)
        return sanitized
    if isinstance(obj, (list, list)):
        return [_sanitize_payload(item, keep_service_ports=keep_service_ports) for item in obj]
    if isinstance(obj, str):
        return _PRIVATE_IP_RE.sub("[redacted]", obj)
    return obj


def contains_private_ip(payload: Any) -> bool:
    """Test helper: True if any private/Tailscale/loopback IP leaks through."""
    try:
        return bool(_PRIVATE_IP_RE.search(json.dumps(payload)))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Automatic archetype inference engine (SPEC-HK-009 §3.2)
# ---------------------------------------------------------------------------

VALID_ARCHETYPES = ("mini_pc", "tower", "phone", "laptop", "tablet", "server_rack")

# Preset variant -> parent archetype (SPEC-HK-009 §3.3). Unknown variants fall
# back to the parent archetype at render time.
VARIANT_TO_ARCHETYPE = {
    "beelink_ser8": "mini_pc",
    "beelink_eqi": "mini_pc",
    "minisforum_ms_s1_max": "tower",
    "generic_tower": "tower",
    "gpd_win_max_2": "laptop",
    "generic_laptop": "laptop",
    "remarkable_2": "tablet",
    "generic_tablet": "tablet",
    "pixel_9_pro": "phone",
    "generic_phone": "phone",
}


def infer_chassis_type(node: Dict[str, Any]) -> str:
    """Infer procedural 3D chassis archetype from declarative node telemetry."""
    if "chassis_type" in node:
        return node["chassis_type"]

    tags = set(t.lower() for t in node.get("tags", []))
    hw = node.get("hardware", {})
    gpu_desc = str(hw.get("gpu", "")).lower()
    role = str(node.get("role", "")).lower()

    # 1. Mobile & Client Endpoints
    if any(t in tags for t in ["phone", "mobile", "android", "ios", "pixel", "iphone"]):
        return "phone"
    if any(t in tags for t in ["tablet", "e-ink", "remarkable", "ipad"]):
        return "tablet"
    if any(t in tags for t in ["laptop", "handheld", "clamshell", "macbook", "bazzite"]):
        return "laptop"

    # 2. Enterprise & Rackmount Servers
    if any(t in tags for t in ["rack", "1u", "2u", "supermicro", "poweredge"]):
        return "server_rack"

    # 3. High-Power Desktop Towers & AI Workstations
    # Conditions: Discrete PCIe GPU, massive VRAM (>64GB), or heavy inference worker role
    if (hw.get("vram_gb", 0) >= 64 or
        any(k in gpu_desc for k in ["rtx", "geforce", "quadro", "a100", "h100", "8060s", "strix"]) or
        any(t in tags for t in ["tower", "desktop", "workstation", "strix-halo", "heavy_inference"])):
        return "tower"

    # 4. Universal Default for Homelab Nodes
    # Compact Mini-PCs (Beelink, NUC, Mac Mini, SER8, EQi)
    return "mini_pc"


# ---------------------------------------------------------------------------
# Slot normalization helpers (SPEC-HK-009 §5.1, §6.2)
# ---------------------------------------------------------------------------

def _slot_decode_tok_per_sec(slot: Dict[str, Any]) -> float:
    """Extract decode tokens/sec from a llama.cpp/llama-server slot record."""
    for key in ("decode_per_second", "predicted_per_second", "tokens_per_second", "tok_per_sec"):
        val = slot.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return round(float(val), 1)
    decoded = slot.get("n_decoded", 0) or 0
    predicted_ms = slot.get("predicted_ms", 0) or 0
    if decoded > 0 and predicted_ms > 0:
        return round(decoded / (predicted_ms / 1000.0), 1)
    mean_ms = slot.get("mean_duration_per_token", 0) or 0
    if mean_ms > 0:
        return round(1000.0 / float(mean_ms), 1)
    return 0.0


def _slot_prefill_tok_per_sec(slot: Dict[str, Any]) -> float:
    """Extract prompt/prefill tokens/sec from a llama.cpp/llama-server slot record."""
    for key in ("prompt_per_second", "prefill_tok_per_sec"):
        val = slot.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return round(float(val), 1)
    prompt_tokens = slot.get("n_prompt_tokens_processed", 0) or 0
    prompt_ms = slot.get("t_prompt_processing", 0) or 0
    if prompt_tokens > 0 and prompt_ms > 0:
        return round(prompt_tokens / (prompt_ms / 1000.0), 1)
    return 0.0


def _slot_status(slot: Dict[str, Any]) -> str:
    """Map a raw llama-server slot to visualizer state: idle | prefill | decoding."""
    if not slot.get("is_processing", False):
        return "idle"
    decoded = slot.get("n_decoded", 0) or 0
    prompt_tokens = slot.get("n_prompt_tokens_processed", 0) or 0
    if slot.get("is_prompt", False) or (prompt_tokens > 0 and decoded == 0):
        return "prefill"
    return "decoding"


def normalize_inference_slots(
    raw_slots: List[Dict[str, Any]],
    slots_total: int = 12,
    resident_model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Normalize raw engine slots into exactly `slots_total` visualizer slot
    cubes (Chunkito's Strix Halo ring is 12; pad idle / truncate overflow)."""
    normalized: List[Dict[str, Any]] = []
    for idx in range(slots_total):
        if idx < len(raw_slots):
            raw = raw_slots[idx] or {}
            status = _slot_status(raw)
            tok = (
                _slot_prefill_tok_per_sec(raw)
                if status == "prefill"
                else _slot_decode_tok_per_sec(raw)
            )
            entry: Dict[str, Any] = {
                "slot_id": raw.get("id", idx) if isinstance(raw.get("id", idx), int) else idx,
                "status": status,
                "tok_per_sec": tok if status != "idle" else 0.0,
                "model": raw.get("model") or (resident_model if status != "idle" else None),
            }
            # Persona (swarm agent occupying the slot) if the engine exposes it.
            persona = raw.get("persona") or raw.get("swarm_agent")
            if persona:
                entry["persona"] = str(persona)
            normalized.append(entry)
        else:
            normalized.append({
                "slot_id": idx,
                "status": "idle",
                "tok_per_sec": 0.0,
                "model": None,
            })
    return normalized


# ---------------------------------------------------------------------------
# Declarative homelab topology registry (SPEC-HK-009 §6.1, blueprint §2)
# ---------------------------------------------------------------------------

DATUM_PLANE: Dict[str, Any] = {
    "size": [40, 40],
    "grid_spacing": 2.0,
    "color": "#1e293b",
}

def _node(
    node_id: str,
    name: str,
    role: str,
    chassis_type: str,
    spatial: Tuple[List[float], List[float]],
    topology: Tuple[List[float], List[float]],
    hardware: Dict[str, Any],
    services: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "name": name,
        "role": role,
        "chassis_type": chassis_type,
        # An explicit bare archetype (e.g. "tower" from the dynamic fleet merge)
        # is its own variant: pass it through instead of re-inferring from
        # role+hardware alone (which would silently demote it to mini_pc).
        "chassis_archetype": VARIANT_TO_ARCHETYPE.get(chassis_type) or (
            chassis_type if chassis_type in VALID_ARCHETYPES
            else infer_chassis_type({"tags": [role], "hardware": hardware})
        ),
        # Backwards-compatible top-level position/rotation == spatial layout
        # (SPEC-HK-009 §6.1 contract), plus explicit dual-mode coordinates for
        # client-side spherical lerp (blueprint §2).
        "position": list(spatial[0]),
        "rotation": list(spatial[1]),
        "layout": {
            "spatial": {"position": list(spatial[0]), "rotation": list(spatial[1])},
            "topology": {"position": list(topology[0]), "rotation": list(topology[1])},
        },
        "hardware": dict(hardware),
        "services": [dict(s) for s in services],
    }


CORE_TOPOLOGY_NODES: List[Dict[str, Any]] = [
    _node(
        "beehive", "Beehive (Coordinator)", "coordinator", "beelink_ser8",
        spatial=([0.0, 0.45, 0.0], [0.0, 0.0, 0.0]),
        topology=([0.0, 0.0, 0.0], [0.0, 0.0, 0.0]),
        hardware={
            "cpu": "AMD Ryzen 7 8845HS (8C/16T)",
            "ram_gb": 32,
            "gpu": "AMD Radeon 780M (gfx1103)",
            "vram_gb": 16,
            "thermal_limit_c": 95,
        },
        services=[
            {"id": "hermes_runtime", "name": "Hermes Gateway", "port": 8787, "icon": "bot", "status": "active"},
            {"id": "hermes_karma", "name": "Hermes Karma", "port": 8020, "icon": "activity", "status": "active"},
            {"id": "neo4j", "name": "Neo4j Graph DB", "port": 7474, "icon": "database", "status": "active"},
            {"id": "cloudflared", "name": "Cloudflare Tunnel", "icon": "shield", "status": "active"},
            {"id": "silverbullet", "name": "SilverBullet Notes", "port": 3000, "icon": "file-text", "status": "active"},
            {"id": "remarkable_ocr", "name": "reMarkable OCR Timer", "icon": "pen-tool", "status": "active"},
        ],
    ),
    _node(
        "chunkito", "Chunkito (APU Inference)", "inference_worker", "minisforum_ms_s1_max",
        spatial=([7.0, 0.8, -2.0], [0.0, -0.2, 0.0]),
        topology=([0.0, 2.5, -6.0], [0.0, -0.2, 0.0]),
        hardware={
            "cpu": "AMD Ryzen AI Max+ 395 (16C/32T)",
            "ram_gb": 128,
            "gpu": "AMD Radeon 8060S (gfx1151, 40 CUs)",
            "vram_gb": 118,
            "inference_slots": 12,
            "thermal_limit_c": 100,
        },
        services=[
            {"id": "llama_server", "name": "ROCm llama-server", "port": 11434, "icon": "cpu", "status": "active"},
            {"id": "woods_gate", "name": "Woods Gate Daemon", "icon": "git-branch", "status": "active"},
            {"id": "node_exporter", "name": "Prometheus Node Exporter", "port": 9100, "icon": "activity", "status": "active"},
        ],
    ),
    _node(
        "chubbs", "Chubbs (GPD Win Max 2)", "mobile_workstation", "gpd_win_max_2",
        spatial=([-6.0, 0.2, 3.0], [0.0, 0.35, 0.0]),
        # Edge satellite satellite, blueprint §2.2 (r ~ 5.5, 120 deg sector)
        topology=([-2.75, 0.2, 4.76], [0.0, 0.35, 0.0]),
        hardware={
            "cpu": "AMD Ryzen 7 7840U (8C/16T)",
            "ram_gb": 32,
            "gpu": "AMD Radeon 780M (gfx1103)",
            "vram_gb": 8,
            "thermal_limit_c": 95,
        },
        services=[
            {"id": "bazzite", "name": "Bazzite OSTree", "icon": "hard-drive", "status": "active"},
            {"id": "syncthing", "name": "Syncthing Daemon", "icon": "refresh-cw", "status": "active"},
            {"id": "distrobox", "name": "Distrobox Dev", "icon": "box", "status": "active"},
            {"id": "nerddictation", "name": "NerdDictation", "icon": "mic", "status": "active"},
        ],
    ),
    _node(
        "remarkable", "reMarkable 2", "digital_paper", "remarkable_2",
        spatial=([-5.5, 0.05, -4.5], [-1.57, 0.0, 0.2]),
        # Edge satellite, blueprint §2.2 (210 deg sector)
        topology=([-4.76, 0.05, -2.75], [-1.57, 0.0, 0.2]),
        hardware={
            "cpu": "ARM Cortex-A8 (i.MX6, 1GHz)",
            "ram_gb": 1,
            "storage_gb": 8,
            "gpu": None,
            "vram_gb": 0,
        },
        services=[
            {"id": "codex_os", "name": "Codex OS", "icon": "file", "status": "active"},
            {"id": "canvas_db", "name": "CANVAS Stroke DB", "icon": "pen-tool", "status": "active"},
            {"id": "tailscale_ssh", "name": "Tailscale SSH", "icon": "shield", "status": "active"},
        ],
    ),
    _node(
        "carmel_edge", "Carmel Edge (Beelink EQi)", "edge_gateway", "beelink_eqi",
        spatial=([6.5, 0.4, 5.5], [0.0, -0.4, 0.0]),
        # Edge satellite, blueprint §2.2 (30 deg sector)
        topology=([4.76, 0.4, 2.75], [0.0, -0.4, 0.0]),
        hardware={
            "cpu": "Intel Core 3 304 (4C/4T)",
            "ram_gb": 16,
            "gpu": "Intel UHD Graphics (QuickSync VAAPI)",
            "vram_gb": 2,
            "thermal_limit_c": 105,
        },
        services=[
            {"id": "frigate", "name": "Frigate NVR", "port": 5000, "icon": "video", "status": "active"},
            {"id": "go2rtc", "name": "go2rtc RTSP", "port": 1984, "icon": "video", "status": "active"},
            {"id": "quicksync", "name": "Intel QuickSync VAAPI", "icon": "cpu", "status": "active"},
            {"id": "hermes_edge", "name": "Hermes Edge", "port": 8644, "icon": "bot", "status": "active"},
            {"id": "mosquitto", "name": "Mosquitto MQTT", "port": 1883, "icon": "radio", "status": "active"},
        ],
    ),
    _node(
        "pixel_9_pro", "Google Pixel 9 Pro", "mobile_client", "pixel_9_pro",
        spatial=([-2.0, 0.05, 5.0], [-1.57, 0.0, -0.1]),
        # Edge satellite, blueprint §2.2 (300 deg sector)
        topology=([2.75, 0.05, -4.76], [-1.57, 0.0, -0.1]),
        hardware={
            "cpu": "Google Tensor 4",
            "ram_gb": 16,
            "gpu": "ARM Mali-G715 MP7",
            "vram_gb": 0,
        },
        services=[
            {"id": "syncthing_android", "name": "Syncthing Android", "icon": "smartphone", "status": "active"},
            {"id": "hermes_pwa", "name": "Hermes PWA", "icon": "globe", "status": "active"},
            {"id": "telegram_bot", "name": "Telegram Bot Client", "icon": "send", "status": "active"},
        ],
    ),
]

CONDUITS: List[Dict[str, Any]] = [
    {
        "id": "inference_highway",
        "type": "inference",
        "source_node": "beehive",
        "target_node": "chunkito",
        "label": "LLM Inference Highway (12 Slots)",
        "arc_height": 2.2,
        "base_color": "#00ffcc",
    },
    {
        "id": "vault_sync_chubbs",
        "type": "syncthing",
        "source_node": "beehive",
        "target_node": "chubbs",
        "label": "Syncthing Obsidian Vault Sync",
        "arc_height": 1.4,
        "base_color": "#8b5cf6",
    },
    {
        "id": "ocr_ingest_remarkable",
        "type": "ocr_batch",
        "source_node": "remarkable",
        "target_node": "beehive",
        "label": "reMarkable Stroke OCR Pipeline",
        "arc_height": 1.6,
        "base_color": "#10b981",
    },
    {
        "id": "carmel_surveillance_neo4j",
        "type": "surveillance",
        "source_node": "carmel_edge",
        "target_node": "beehive",
        "label": "Frigate NVR Event Cypher Triples",
        "arc_height": 1.8,
        "base_color": "#f59e0b",
    },
]


# ---------------------------------------------------------------------------
# VisualizerService
# ---------------------------------------------------------------------------

class VisualizerService:
    """Owns the declarative topology model and the live SSE telemetry stream."""

    def __init__(self):
        self._topology_nodes = CORE_TOPOLOGY_NODES
        self._conduits = CONDUITS

    # -- chassis inference (SPEC-HK-009 §3.2) --------------------------------

    def infer_chassis_type(self, node: Dict[str, Any]) -> str:
        """Infer procedural 3D chassis archetype from declarative node telemetry."""
        return infer_chassis_type(node)

    # -- static topology contract (SPEC-HK-009 §6.1) --------------------------

    def get_topology(self) -> Dict[str, Any]:
        """Return the full declarative homelab layout: nodes, chassis specs,
        docked services, conduit definitions, and the datum plane.

        Extra fleet nodes configured via SPEC-HK-008 (fleet.yaml /
        HERMES_KARMA_NODES_JSON) that are not part of the bespoke core six are
        appended with auto-inferred archetypes and sanitized metadata.
        """
        nodes = copy.deepcopy(self._topology_nodes)
        core_ids = {n["id"] for n in nodes}

        # Portable-deployment extension: infer chassis for arbitrary nodes.
        try:
            for cfg in node_collector.get_configured_nodes():
                cfg_id = str(cfg.get("id", "")).strip()
                if not cfg_id or cfg_id in core_ids:
                    continue
                chassis = infer_chassis_type({
                    "tags": cfg.get("tags", []),
                    "hardware": cfg.get("hardware", {}),
                    "role": cfg.get("role", "worker"),
                })
                nodes.append(_node(
                    cfg_id,
                    str(cfg.get("name", cfg_id)),
                    str(cfg.get("role", "worker")),
                    chassis,
                    spatial=([0.0, 0.3, 0.0], [0.0, 0.0, 0.0]),
                    topology=([0.0, 0.0, 0.0], [0.0, 0.0, 0.0]),
                    hardware=_sanitize_payload(cfg.get("hardware", {})),
                    services=[],
                ))
        except Exception as exc:  # never break the topology contract
            logger.warning("Visualizer dynamic node merge skipped: %s", exc)

        topology = {
            "datum_plane": copy.deepcopy(DATUM_PLANE),
            "nodes": nodes,
            "conduits": copy.deepcopy(self._conduits),
            "layout_modes": ["spatial", "topology"],
            "node_count": len(nodes),
            "conduit_count": len(self._conduits),
            "spec": "SPEC-HK-009",
        }
        # Topology is a static declarative contract: declared docked-service
        # ports are kept (SPEC-HK-009 §6.1), but any stray IP material is
        # still redacted and host/tailscale keys are stripped.
        return _sanitize_payload(topology, keep_service_ports=True)

    # -- live stream helpers ---------------------------------------------------

    def _offline_delta(self) -> Dict[str, Any]:
        return {
            "online": False,
            "cpu_pct": 0.0,
            "gpu_busy_pct": 0.0,
            "temperature_c": None,
            "power_w": None,
            "vram_used_gb": 0.0,
            "vram_total_gb": 0.0,
            "ram_used_gb": 0.0,
            "ram_total_gb": 0.0,
            "latency_ms": None,
        }

    def _node_delta(self, telemetry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Map one NodeTelemetryCollector record to a lightweight visualizer
        delta. Any failure (unreachable/partial node) degrades to offline."""
        if not telemetry or telemetry.get("status") not in ("online", "warning"):
            return self._offline_delta()
        try:
            amdgpu = telemetry.get("amdgpu") or {}
            apu_vram = telemetry.get("apu_vram") or {}
            cpu = telemetry.get("cpu") or {}
            memory = telemetry.get("memory") or {}

            temperature = amdgpu.get("temperature_c")
            if temperature is None:
                temperature = cpu.get("temperature_c")

            return {
                "online": True,
                "cpu_pct": float(cpu.get("utilization_percent", 0.0) or 0.0),
                "gpu_busy_pct": float(amdgpu.get("gpu_busy_percent", 0.0) or 0.0),
                "temperature_c": float(temperature) if temperature is not None else None,
                "power_w": float(amdgpu["power_w"]) if amdgpu.get("power_w") is not None else None,
                "vram_used_gb": float(apu_vram.get("used_gb", 0.0) or 0.0),
                "vram_total_gb": float(apu_vram.get("total_gb", 0.0) or 0.0),
                "ram_used_gb": float(memory.get("used_gb", 0.0) or 0.0),
                "ram_total_gb": float(memory.get("total_gb", 0.0) or 0.0),
                "latency_ms": telemetry.get("latency_ms"),
            }
        except Exception:
            return self._offline_delta()

    def _inference_delta(self, telemetry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Chunkito llama-server inference block: resident model, instantaneous
        throughput, and the normalized 12-slot ring (SPEC-HK-009 §5.1/§6.2)."""
        slots_total = 12
        resident_model: Optional[str] = None
        raw_slots: List[Dict[str, Any]] = []

        if telemetry and telemetry.get("status") in ("online", "warning"):
            try:
                hw = telemetry.get("hardware") or {}
                if isinstance(hw.get("inference_slots"), int) and hw["inference_slots"] > 0:
                    slots_total = hw["inference_slots"]
                inf = telemetry.get("inference_engine") or {}
                loaded = inf.get("loaded_models") or []
                if loaded:
                    resident_model = loaded[0].get("name") or loaded[0].get("model")
                raw_slots = [s for s in (inf.get("slots") or []) if isinstance(s, dict)]
            except Exception:
                raw_slots = []

        slots = normalize_inference_slots(raw_slots, slots_total=slots_total, resident_model=resident_model)
        active = sum(1 for s in slots if s["status"] != "idle")
        total_tok = round(sum(s["tok_per_sec"] for s in slots), 1)
        prefilling = any(s["status"] == "prefill" for s in slots)

        online = bool(telemetry and telemetry.get("status") in ("online", "warning"))
        return {
            "online": online,
            "resident_model": resident_model,
            "instant_tok_per_sec": total_tok if online else 0.0,
            "slots_active": active,
            "slots_total": slots_total,
            "prefill_burst": prefilling,
            "slots": slots,
        }

    def _build_stream_payload(self, telemetry_list: List[Dict[str, Any]], error: Optional[str] = None) -> Dict[str, Any]:
        """Assemble the 1Hz SSE delta frame (SPEC-HK-009 §6.2)."""
        by_id: Dict[str, Dict[str, Any]] = {}
        for t in telemetry_list or []:
            if isinstance(t, dict) and t.get("node_id"):
                by_id[str(t["node_id"])] = t

        nodes_payload: Dict[str, Any] = {}
        for topo_node in self._topology_nodes:
            nid = topo_node["id"]
            delta = self._node_delta(by_id.get(nid))
            if nid == "chunkito":
                delta["inference"] = self._inference_delta(by_id.get(nid))
            nodes_payload[nid] = delta

        # Dynamic fleet nodes (SPEC-HK-008) beyond the core six
        core_ids = {n["id"] for n in self._topology_nodes}
        for nid, t in by_id.items():
            if nid not in core_ids:
                nodes_payload[nid] = self._node_delta(t)

        chunkito = nodes_payload.get("chunkito", self._offline_delta())
        chunkito_inf = chunkito.get("inference", {})

        def _latency_of(nid: str) -> Optional[float]:
            delta = nodes_payload.get(nid) or {}
            return delta.get("latency_ms")

        conduits_payload: Dict[str, Any] = {}
        for conduit in self._conduits:
            cid = conduit["id"]
            src = nodes_payload.get(conduit["source_node"], {})
            dst = nodes_payload.get(conduit["target_node"], {})
            active = bool(src.get("online") and dst.get("online"))
            entry: Dict[str, Any] = {
                "active": active,
                "activity_level": 0.0,
                "latency_ms": _latency_of(conduit["target_node"]) or _latency_of(conduit["source_node"]),
            }
            if conduit["type"] == "inference":
                tok = float(chunkito_inf.get("instant_tok_per_sec", 0.0) or 0.0)
                entry["tokens_per_sec"] = tok
                entry["activity_level"] = round(min(1.0, tok / 250.0), 3)
                entry["surge"] = bool(chunkito_inf.get("prefill_burst")) or tok > 100.0
            elif conduit["type"] == "syncthing":
                entry["bytes_per_sec"] = 0
            conduits_payload[cid] = entry

        payload: Dict[str, Any] = {
            "timestamp": round(time.time(), 2),
            "nodes": nodes_payload,
            "conduits": conduits_payload,
        }
        if error:
            payload["collector_error"] = "telemetry collector unavailable"
        return _sanitize_payload(payload)

    # -- SSE stream (SPEC-HK-009 §6.2) ----------------------------------------

    async def get_telemetry_stream(
        self,
        interval: float = 1.0,
        max_events: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """Yield SSE frames `data: <json>\\n\\n` at ~1Hz.

        The stream never raises: collector failures emit a degraded (all
        offline) frame instead, and unreachable individual nodes degrade to
        `{"online": false}` deltas. `max_events` bounds the generator for
        automated tests; production callers stream indefinitely until the
        client disconnects.
        """
        emitted = 0
        while True:
            error: Optional[str] = None
            telemetry_list: List[Dict[str, Any]] = []
            try:
                telemetry_list = await node_collector.get_all_nodes_telemetry()
            except Exception as exc:
                error = str(exc)
                logger.warning("Visualizer stream collector failure: %s", exc)

            try:
                payload = self._build_stream_payload(telemetry_list, error=error)
            except Exception as exc:  # absolute last-resort frame
                logger.error("Visualizer stream payload build failure: %s", exc)
                payload = {
                    "timestamp": round(time.time(), 2),
                    "nodes": {},
                    "conduits": {},
                    "collector_error": "payload build failure",
                }

            yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
            emitted += 1
            if max_events is not None and emitted >= max_events:
                return
            await asyncio.sleep(interval)


visualizer_service = VisualizerService()
