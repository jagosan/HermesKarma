"""Multi-node fleet telemetry API endpoints for Hermes Karma."""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any, List
from api.services.node_collector import node_collector

router = APIRouter(prefix="/api/nodes", tags=["Fleet & Node Telemetry"])


@router.get("")
async def get_nodes(force_refresh: bool = Query(False, description="Bypass cache and query nodes live")):
    """Get telemetry status for all configured fleet nodes (Beehive coordinator + Chunkito remote APU)."""
    nodes = await node_collector.get_all_nodes_telemetry(force_refresh=force_refresh)

    total_nodes = len(nodes)
    online_nodes = sum(1 for n in nodes if n.get("status") in ["online", "warning"])
    total_vram_gb = sum(n.get("apu_vram", {}).get("total_gb", 0) for n in nodes)
    used_vram_gb = sum(n.get("apu_vram", {}).get("used_gb", 0) for n in nodes)
    loaded_models_total = sum(
        n.get("inference_engine", {}).get("loaded_models_count", n.get("ollama", {}).get("loaded_models_count", 0))
        for n in nodes
    )
    active_slots_total = sum(
        n.get("inference_engine", {}).get("slot_summary", {}).get("active_slots", 0) for n in nodes
    )

    return {
        "summary": {
            "total_nodes": total_nodes,
            "online_nodes": online_nodes,
            "offline_nodes": total_nodes - online_nodes,
            "total_vram_gb": round(total_vram_gb, 2),
            "used_vram_gb": round(used_vram_gb, 2),
            "vram_utilization_pct": round((used_vram_gb / total_vram_gb * 100), 1) if total_vram_gb > 0 else 0.0,
            "loaded_models_total": loaded_models_total,
            "active_slots_total": active_slots_total,
        },
        "nodes": nodes,
    }


@router.get("/{node_id}")
async def get_node_detail(node_id: str, force_refresh: bool = Query(False)):
    """Get detailed telemetry and model catalog for a specific node."""
    node = await node_collector.get_node_telemetry(node_id=node_id, force_refresh=force_refresh)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in fleet configuration")
    return node


@router.post("/refresh")
async def refresh_all_nodes():
    """Trigger on-demand live telemetry polling across all fleet nodes."""
    nodes = await node_collector.get_all_nodes_telemetry(force_refresh=True)
    return {
        "success": True,
        "nodes_refreshed": len(nodes),
        "nodes": nodes,
    }


@router.post("/{node_id}/refresh")
async def refresh_single_node(node_id: str):
    """Trigger on-demand live telemetry polling for a specific node."""
    node = await node_collector.get_node_telemetry(node_id=node_id, force_refresh=True)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    return {
        "success": True,
        "node": node,
    }


@router.get("/{node_id}/models")
async def get_node_models(node_id: str):
    """Get active loaded models in VRAM and available models catalog on a node."""
    node = await node_collector.get_node_telemetry(node_id=node_id, force_refresh=False)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    inf = node.get("inference_engine", {})
    ollama = node.get("ollama", {})
    return {
        "node_id": node_id,
        "node_name": node.get("name"),
        "status": node.get("status"),
        "backend_type": inf.get("backend_type", "unknown"),
        "version": inf.get("version") or ollama.get("version"),
        "max_loaded_models": inf.get("max_loaded_models", ollama.get("max_loaded_models", 1)),
        "loaded_models_count": inf.get("loaded_models_count", ollama.get("loaded_models_count", 0)),
        "overloaded": inf.get("overloaded", ollama.get("overloaded", False)),
        "loaded_models": inf.get("loaded_models", ollama.get("loaded_models", [])),
        "available_models": inf.get("available_models", ollama.get("available_models", [])),
        "slots": inf.get("slots", []),
        "slot_summary": inf.get("slot_summary", {}),
        "apu_vram": node.get("apu_vram", {}),
        "amdgpu": node.get("amdgpu", {}),
    }
