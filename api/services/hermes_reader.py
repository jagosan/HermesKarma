"""Hermes Agent Data Reader Service.
Connects to ~/.hermes/state.db in Read-Only WAL mode and parses config, skills, memory, cron, and complete multi-model usage.
"""
import sqlite3
import os
import json
import yaml
import re
import difflib
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from api.config import (
    HERMES_DIR,
    HERMES_STATE_DB,
    HERMES_CONFIG_YAML,
    HERMES_MEMORY_FILE,
    HERMES_USER_FILE,
    HERMES_SKILLS_DIR,
    HERMES_CRON_DIR,
    HERMES_PROFILES_DIR,
    KARMA_LIVE_DIR,
)
from api.services.metadata_service import metadata_service
from api.services.pricing_engine import pricing_engine


# ---------------------------------------------------------------------------
# Inference throughput baselines (SPEC-HK-005 / TASK-HK-105)
#
# Empirical decode (generation) speeds in tokens/second for the pantheon
# roster. Live telemetry (`llamacpp:predicted_tokens_seconds` on chunkito)
# is preferred when reachable; these baselines are the graceful fallback so
# the gauge / velocity HUD always renders.
# ---------------------------------------------------------------------------
APU_DECODE_TOK_PER_SEC = 35.0            # Strix Halo nominal APU decode baseline (~35 tok/s for 125B/6B MoE w/ MTP)
APU_PREFILL_TOK_PER_SEC = 294.0          # Strix Halo (chunkito) prompt processing (~294 tok/s measured)
CLOUD_DECODE_TOK_PER_SEC = 145.0         # Cloud frontier aggregate reference
BEEHIVE_OLLAMA_TOK_PER_SEC = 22.0        # beehive CPU/Ollama mid-band (18-25)
CLOUD_DEFAULT_TOK_PER_SEC = 80.0         # unknown cloud model
LOCAL_DEFAULT_TOK_PER_SEC = 25.0         # unknown local model

THROUGHPUT_TIERS = ("ultra", "fast", "standard", "local_apu")

# Exact model-name baselines (lower-cased comparison keys).
# Differentiated empirical decode speeds (tok/s) on Strix Halo APU (Radeon 8060S gfx1151)
# and Beehive nodes, reflecting true model parameter weights and quantizations.
MODEL_THROUGHPUT_BASELINES: Dict[str, Any] = {
    # Frontier cloud models
    "gemini-3.8-flash": (150.0, "ultra"),
    "gemini-3.7-flash": (135.0, "fast"),
    "gemini-3.6-flash": (122.0, "fast"),
    "gemini-3.5-flash": (110.0, "fast"),
    "gemini-3.1-pro-preview": (62.0, "standard"),
    "gemini-3.1-pro": (60.0, "standard"),
    "gemini-3-flash-preview": (95.0, "standard"),
    "qwen/qwen3.8-flash": (150.0, "ultra"),
    "claude-opus-5": (48.0, "standard"),
    "claude-opus-4": (45.0, "standard"),
    "claude-3-opus": (45.0, "standard"),
    "claude-sonnet-latest": (68.0, "standard"),

    # Chunkito Strix Halo APU serving queue (Radeon 8060S gfx1151, unified memory)
    # Calibrated by model architecture:
    # Qwen3.8-Flash-Next (125B / 6B active MoE with Multi-Token Prediction): 34.0–40.5 tok/s measured
    "qwen3.8-flash-next:262k": (35.0, "local_apu"),
    "qwen3.8-flash-next": (35.0, "local_apu"),
    "flash-next": (35.0, "local_apu"),
    "jagular": (35.0, "local_apu"),
    "qwen3-coder-next:q8_0": (16.5, "local_apu"),      # 80B Q8_0 with draft-mtp sidecar
    "qwen3-coder-next:262k": (15.5, "local_apu"),     # 80B at long context
    "qwen3-coder-next": (16.0, "local_apu"),
    "deepseek-v4:262k": (15.1, "local_apu"),           # 284B / ~13B active MoE measured at 15.1 tok/s
    "deepseek-v4:96k": (15.1, "local_apu"),
    "deepseek-v4": (15.1, "local_apu"),
    "heffalump": (15.1, "local_apu"),
    "qwen3.8-27b": (28.0, "local_apu"),               # 27B dense ~28 tok/s
    "qwen3-coder-30b:262k": (25.5, "local_apu"),
    "qwen3-coder-30b:128k": (27.0, "local_apu"),
    "qwen3-coder-30b": (26.5, "local_apu"),
    "hf.co/unsloth/qwen3-30b-a3b-instruct-2507-gguf:ud-iq2_m": (31.0, "local_apu"),
    "hf.co/unsloth/qwen3.6-35b-a3b-gguf:ud-iq2_m": (28.5, "local_apu"),
    "qwen2.5:14b": (41.5, "local_apu"),              # 14B dense ~41.5 tok/s
    "gpt-oss:20b": (34.0, "local_apu"),               # 20B dense ~34 tok/s
    "gemma4:12b": (44.0, "local_apu"),                # 12B dense ~44 tok/s

    # Beehive Ollama roster (CPU / DDR5, 18-25 tok/s band)
    "ernie-fixed": (20.0, "local_apu"),
    "qwen3.5:latest": (22.0, "local_apu"),
    "glm4:9b": (18.0, "local_apu"),
}

# Ordered pattern rules — first match wins, so keep specific → generic.
_THROUGHPUT_PATTERN_RULES = (
    # Qwen3.8-Flash-Next 125B/6B active MoE with MTP
    (("flash-next", "jagular"), 35.0, "local_apu"),
    # 80B MoE models
    (("coder-next", "80b"), 16.5, "local_apu"),
    # DeepSeek-V4 MoE
    (("deepseek", "heffalump"), 15.1, "local_apu"),
    # 27B - 35B dense models
    (("27b", "30b", "35b"), 27.5, "local_apu"),
    # 14B - 20B models
    (("14b", "20b"), 36.0, "local_apu"),
    # Small local models (<14B)
    (("8b", "9b", "12b"), 42.0, "local_apu"),
    # Generic APU fallback
    (("chunkito", "strix", "8060s", "262k", "128k"), 35.0, "local_apu"),
    # Cloud tiers
    (("pro",), 60.0, "standard"),
    (("opus",), 45.0, "standard"),
    (("sonnet",), 68.0, "standard"),
    (("haiku",), 105.0, "fast"),
    (("flash",), 125.0, "fast"),
    # Local quant / Ollama fallback
    (("gguf", ":q", ":iq", ":ud-", "ollama", ":latest"), BEEHIVE_OLLAMA_TOK_PER_SEC, "local_apu"),
)

_CHUNKITO_LIVE_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}

def get_live_chunkito_throughput() -> Optional[Dict[str, Any]]:
    """Probe remote inference node (e.g. chunkito) llama-server Prometheus metrics for live measured throughput."""
    now = time.time()
    if now - _CHUNKITO_LIVE_CACHE["ts"] < 15.0 and _CHUNKITO_LIVE_CACHE["data"] is not None:
        return _CHUNKITO_LIVE_CACHE["data"]
    remote_host = os.getenv("CHUNKITO_HOST") or os.getenv("CHUNKITO_TAILSCALE_IP") or os.getenv("REMOTE_WORKER_HOST")
    if not remote_host:
        return None
    port = int(os.getenv("CHUNKITO_OLLAMA_PORT", os.getenv("REMOTE_WORKER_OLLAMA_PORT", 11434)))
    try:
        import urllib.request, re
        req = urllib.request.Request(f"http://{remote_host}:{port}/metrics", headers={"User-Agent": "HermesKarma/1.0"})
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            text = resp.read().decode("utf-8")
            dec_g = re.search(r'^llamacpp:predicted_tokens_seconds\s+([0-9.]+)', text, re.M)
            pref_g = re.search(r'^llamacpp:prompt_tokens_seconds\s+([0-9.]+)', text, re.M)
            tok_tot = re.search(r'^llamacpp:tokens_predicted_total\s+([0-9.e+-]+)', text, re.M)
            sec_tot = re.search(r'^llamacpp:tokens_predicted_seconds_total\s+([0-9.e+-]+)', text, re.M)

            inst_dec = float(dec_g.group(1)) if dec_g else 0.0
            inst_pref = float(pref_g.group(1)) if pref_g else 0.0
            cum_dec = (float(tok_tot.group(1)) / float(sec_tot.group(1))) if (tok_tot and sec_tot and float(sec_tot.group(1)) > 0) else 0.0

            eff_dec = inst_dec if inst_dec > 0 else (cum_dec if cum_dec > 0 else 0.0)
            data = {
                "decode_tok_s": round(eff_dec, 1),
                "prefill_tok_s": round(inst_pref, 1),
                "cumulative_decode_tok_s": round(cum_dec, 1),
                "is_live": True
            }
            _CHUNKITO_LIVE_CACHE["ts"] = now
            _CHUNKITO_LIVE_CACHE["data"] = data
            return data
    except Exception:
        return None


def model_throughput_profile(model_name: str, is_local: Optional[bool] = None) -> tuple:
    """Return ``(throughput_tok_per_sec, throughput_tier)`` for a model.

    Uses the empirical baseline table, then ordered pattern rules, then a
    cloud/local default. Pure function of the model name so the analytics
    payload stays deterministic and cache-friendly.
    """
    m = (model_name or "").strip().lower()
    if is_local is None:
        is_local = any(k in m for k in (
            "qwen", "gemma", "deepseek", "gpt-oss", "gguf", "chunkito",
            "llama", "mistral", "phi", "strix", "local", "ernie", "glm",
            ":q8_", ":iq", ":ud-",
        ))

    if m in MODEL_THROUGHPUT_BASELINES:
        tok, tier = MODEL_THROUGHPUT_BASELINES[m]
        return float(tok), tier

    for needles, tok, tier in _THROUGHPUT_PATTERN_RULES:
        if any(n in m for n in needles):
            return float(tok), tier

    if is_local:
        return LOCAL_DEFAULT_TOK_PER_SEC, "local_apu"
    return CLOUD_DEFAULT_TOK_PER_SEC, "standard"


# Longest-context local model advertised on the instrument cluster.
LONGEST_CONTEXT_LOCAL_MODEL = "qwen3.8-flash-next:262k"


# ---------------------------------------------------------------------------
# SPEC-HK-007: 4-Tier Context Tax, Spatial Navigation ROI & Harness Telemetry
#
# Bucket model (mutually exclusive, exhaustive over a session's input tokens):
#   T1 cold_start_tokens        — system prompt, MEMORY.md/USER.md, active
#                                 SKILL.md router descriptions, tool/MCP schemas.
#                                 Explicit hook emission wins, then Turn-1
#                                 baseline minus user prompt, then system
#                                 prompt token estimate.
#   T2 spatial_index_tokens     — docs/MAP.md, 00_AI_CONTEXT_OVERVIEW.md and
#                                 graphify topology queries.
#   T3 exploration_waste_tokens — reads of files never edited in the session;
#                                 in pure Q&A sessions (0 edits) only redundant
#                                 re-reads and failed/empty search sweeps count.
#   T4 productive_tokens        — everything else, computed by subtraction so
#                                 the strict invariant
#                                 T1 + T2 + T3 + T4 == total_prompt_tokens
#                                 always holds (reconcile_context_buckets).
# ---------------------------------------------------------------------------

INGRESS_CHANNELS = ("cli", "webui", "telegram", "cron", "pantheon_subagent")

_SPATIAL_INDEX_FILE_MARKERS = ("map.md", "00_ai_context_overview.md")
_GRAPHIFY_MARKERS = ("graphify", ".graphify", "symbol_graph")

_READ_TOOL_NAMES = {
    "read_file", "read", "view_file", "view", "cat", "grep", "grep_code",
    "search_files", "ripgrep", "find", "ls", "list_files", "list_directory",
    "read_many_files", "codebase_retrieval", "search_code", "glob",
}
_EDIT_TOOL_NAMES = {
    "write_file", "write", "edit_file", "edit", "patch", "patch_file",
    "multi_edit", "create_file", "create", "replace", "str_replace_editor",
    "write_to_file", "delete_file", "move_file",
}

_SPATIAL_GUIDANCE_FIRST_TURNS = 3   # spec 3.2A: MAP.md read within first 3 turns
_AMNESIA_REREAD_WINDOW_TURNS = 5    # spec 3.3: K assistant turns after compaction
_TOP_TRASH_LIMIT = 10
_MAP_DRIFT_COMMIT_THRESHOLD = int(os.getenv("HERMES_KARMA_MAP_DRIFT_COMMITS", "15"))
_MAX_AUDITED_SESSIONS = int(os.getenv("HERMES_KARMA_MAX_AUDIT_SESSIONS", "2000"))
_DEFAULT_INPUT_USD_PER_MILLION = 0.75  # counterfactual floor (Gemini Flash class)

# SPEC-HK-007 §3.4: Skill ROI & Dead Skill auditor.
_SKILL_VIEW_TOOL_NAMES = {
    "skill_view", "skill", "use_skill", "run_skill", "load_skill",
    "skills_view", "view_skill",
}
_SKILL_MANAGE_TOOL_NAMES = {"skill_manage", "skill_create", "skill_update"}
_SKILL_ROI_TTL_SECONDS = float(os.getenv("HERMES_KARMA_SKILL_ROI_TTL", "120"))


def _tool_output_is_failure(content: str) -> bool:
    """Heuristic tool-failure detector for role=tool message bodies (§3.4)."""
    low = (content or "")[:300].lower()
    if not (content or "").strip():
        return True
    return (
        "no match" in low or "no matches" in low
        or "no results" in low or "0 results" in low
        or "not found" in low or low.startswith("error")
        or "traceback (most recent call last)" in low
    )


def estimate_tokens(text: Optional[str]) -> int:
    """Cheap deterministic token estimate (~4 chars/token) when no exact count exists."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def reconcile_context_buckets(total_prompt_tokens: Any, cold_start: Any,
                              spatial_index: Any, exploration: Any) -> tuple:
    """SPEC-HK-007 §3.1: force ``T1 + T2 + T3 + T4 == total_prompt_tokens``.

    Returns ``(t1, t2, t3, t4)`` of non-negative ints summing exactly to the
    (non-negative) total. Over-attribution is trimmed exploration-first, then
    spatial, then cold start; the residual always lands in the productive
    bucket so the four tiers can never double-count or under-count the total.
    """
    total = max(0, int(total_prompt_tokens or 0))
    t1 = max(0, int(cold_start or 0))
    t2 = max(0, int(spatial_index or 0))
    t3 = max(0, int(exploration or 0))
    over = (t1 + t2 + t3) - total
    if over > 0:
        take = min(t3, over); t3 -= take; over -= take
        take = min(t2, over); t2 -= take; over -= take
        take = min(t1, over); t1 -= take; over -= take
    return t1, t2, t3, total - t1 - t2 - t3


def classify_ingress_channel(source: Optional[str] = None,
                             parent_session_id: Optional[str] = None,
                             platform: Optional[str] = None) -> str:
    """Map a Hermes session source/platform to its SPEC-HK-007 §3.5 ingress gateway."""
    key = (source or platform or "").strip().lower()
    if parent_session_id or key in ("subagent", "pantheon", "delegation", "swarm"):
        return "pantheon_subagent"
    if key == "telegram":
        return "telegram"
    if key in ("cron", "scheduler", "scheduled"):
        return "cron"
    if key in ("webui", "dashboard", "web"):
        return "webui"
    return "cli"


class HermesReader:
    def __init__(self, db_path=HERMES_STATE_DB):
        self.db_path = Path(db_path)

    def _get_ro_conn(self) -> Optional[sqlite3.Connection]:
        if not self.db_path.exists():
            return None
        # Connect in read-only mode using SQLite URI
        uri = f"file:{self.db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA query_only = ON;")
        return conn

    def _get_all_state_dbs(self) -> List[Path]:
        """Return all state.db paths: primary ~/.hermes/state.db and all ~/.hermes/profiles/*/state.db."""
        dbs = []
        if self.db_path.exists():
            dbs.append(self.db_path)
        profiles_dir = HERMES_DIR / "profiles"
        if profiles_dir.exists():
            for p_db in sorted(profiles_dir.glob("*/state.db")):
                if p_db.is_file() and p_db != self.db_path:
                    dbs.append(p_db)
        return dbs

    def _get_ro_conn_for_db(self, db_path: Path) -> Optional[sqlite3.Connection]:
        if not db_path.exists():
            return None
        uri = f"file:{db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA query_only = ON;")
        return conn

    def _find_db_for_session(self, session_id: str) -> Optional[sqlite3.Connection]:
        """Locate which state.db contains the session_id and return an active read-only connection."""
        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,))
                if cur.fetchone():
                    return conn
                conn.close()
            except Exception:
                conn.close()
        return None

    def get_all_distinct_models(self) -> List[Dict[str, Any]]:
        """Retrieve all distinct models used across sessions, subagents, and config across all state databases."""
        models_map = {}
        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            with conn:
                cur = conn.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                # 1. From session_model_usage
                if "session_model_usage" in tables:
                    try:
                        cur.execute("""
                            SELECT 
                                model,
                                COALESCE(billing_provider, '') as provider,
                                COUNT(DISTINCT session_id) as session_count,
                                SUM(api_call_count) as total_calls
                            FROM session_model_usage
                            WHERE model IS NOT NULL AND model != ''
                            GROUP BY model
                        """)
                        for r in cur.fetchall():
                            m = dict(r)
                            m_name = m["model"]
                            if m_name not in models_map:
                                models_map[m_name] = {
                                    "name": m_name,
                                    "provider": m.get("provider") or self._classify_provider(m_name),
                                    "session_count": m.get("session_count", 0),
                                    "total_calls": m.get("total_calls", 0),
                                    "is_local": self._is_local_model(m_name, m.get("provider")),
                                }
                            else:
                                models_map[m_name]["session_count"] += m.get("session_count", 0)
                                models_map[m_name]["total_calls"] += m.get("total_calls", 0)
                    except Exception:
                        pass

                # 2. From sessions table
                if "sessions" in tables:
                    try:
                        cur.execute("""
                            SELECT DISTINCT model, COUNT(*) as cnt
                            FROM sessions
                            WHERE model IS NOT NULL AND model != ''
                            GROUP BY model
                        """)
                        for r in cur.fetchall():
                            m_name = r[0]
                            if m_name not in models_map:
                                models_map[m_name] = {
                                    "name": m_name,
                                    "provider": self._classify_provider(m_name),
                                    "session_count": r[1],
                                    "total_calls": r[1],
                                    "is_local": self._is_local_model(m_name, ""),
                                }
                            else:
                                if models_map[m_name]["session_count"] == 0:
                                    models_map[m_name]["session_count"] = r[1]
                    except Exception:
                        pass

        return sorted(list(models_map.values()), key=lambda x: (not x["is_local"], x["name"]))

    def _is_local_model(self, model_name: str, provider: Optional[str] = "") -> bool:
        """Determine if a model is served locally (Ollama / chunkito / llama.cpp / GGUF)."""
        m = (model_name or "").lower()
        p = (provider or "").lower()
        if p in ["ollama", "chunkito", "custom", "local", "vllm", "llamacpp"]:
            return True
        if any(k in m for k in [
            "qwen", "gemma", "deepseek", "gpt-oss", "gguf", "ud-iq2", "chunkito",
            "llama", "mistral", "phi", "strix", "local", ":q8_", ":iq", ":UD-"
        ]):
            return True
        return False

    def _classify_provider(self, model_name: str) -> str:
        """Infer provider category for display tags."""
        m = (model_name or "").lower()
        if "gemini" in m:
            return "Google Gemini"
        elif "claude" in m or "sonnet" in m or "opus" in m:
            return "Anthropic Claude"
        elif "chunkito" in m or "qwen3.8" in m or "qwen3-coder" in m:
            return "Chunkito APU"
        elif "deepseek" in m:
            return "Chunkito / Local"
        elif "qwen" in m or "gemma" in m or "gpt-oss" in m:
            return "Local Ollama / GGUF"
        elif "openrouter" in m:
            return "OpenRouter"
        return "Custom / Self-Hosted"

    def get_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        source: Optional[str] = None,
        model: Optional[str] = None,
        search: Optional[str] = None,
        persona: Optional[str] = None,
        date_from: Optional[float] = None,
        date_to: Optional[float] = None,
    ) -> Dict[str, Any]:
        import time
        if date_from is None and not search and not source and not persona:
            date_from = time.time() - (30 * 86400)

        all_candidates = []
        seen_ids = set()

        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            with conn:
                cur = conn.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "sessions" not in tables:
                    continue

                cols = self._get_table_columns(cur, "sessions")
                conditions = ["hidden = 0" if "hidden" in cols else "1=1"]
                params = []

                if source:
                    conditions.append("source = ?")
                    params.append(source)
                if model:
                    has_usage = "session_model_usage" in tables
                    has_deleg = "async_delegations" in tables
                    model_conds = ["sessions.model LIKE ?"]
                    m_params = [f"%{model}%"]
                    if has_usage:
                        model_conds.append("sessions.id IN (SELECT session_id FROM session_model_usage WHERE model LIKE ?)")
                        m_params.append(f"%{model}%")
                    if has_deleg:
                        model_conds.append("sessions.id IN (SELECT origin_session FROM async_delegations WHERE event_json LIKE ? OR task_json LIKE ?)")
                        m_params.extend([f"%{model}%", f"%{model}%"])
                    conditions.append(f"({' OR '.join(model_conds)})")
                    params.extend(m_params)
                if search:
                    search_cols = []
                    s_params = []
                    for sc in ["title", "id", "cwd", "git_branch"]:
                        if sc in cols:
                            search_cols.append(f"sessions.{sc} LIKE ?")
                            s_params.append(f"%{search}%")
                    if search_cols:
                        conditions.append(f"({' OR '.join(search_cols)})")
                        params.extend(s_params)
                if date_from:
                    conditions.append("sessions.started_at >= ?")
                    params.append(date_from)
                if date_to:
                    conditions.append("sessions.started_at <= ?")
                    params.append(date_to)

                where_clause = " AND ".join(conditions)
                cur.execute(f"SELECT * FROM sessions WHERE {where_clause} ORDER BY started_at DESC", params)
                for r in cur.fetchall():
                    rd = dict(r)
                    sid = rd["id"]
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)
                    # Hydrate first prompt & title if messages table exists
                    if "messages" in tables:
                        try:
                            msg_row = cur.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1", (sid,)).fetchone()
                            if msg_row and msg_row[0]:
                                rd["first_prompt"] = msg_row[0]
                                if not rd.get("title") or str(rd.get("title")).startswith("[Workspace::v1:"):
                                    rd["title"] = msg_row[0][:80].replace("\n", " ").strip()
                            elif not rd.get("title") and (rd.get("source") == "subagent" or rd.get("parent_session_id")):
                                rd["title"] = f"Subagent Task ({sid})"
                        except Exception:
                            if not rd.get("title") and (rd.get("source") == "subagent" or rd.get("parent_session_id")):
                                rd["title"] = f"Subagent Task ({sid})"
                    all_candidates.append(rd)

        # Sort all candidates across all databases by started_at DESC
        all_candidates.sort(key=lambda x: x.get("started_at") or 0, reverse=True)

        # Filter by persona if specified
        if persona:
            matched = []
            for rd in all_candidates:
                attr = self.attribute_session_persona(rd)
                if attr == persona.lower() or (rd.get("profile_name") and rd.get("profile_name").lower() == persona.lower()):
                    matched.append(rd)
            total = len(matched)
            rows = matched[offset:offset + limit]
        else:
            total = len(all_candidates)
            rows = all_candidates[offset:offset + limit]

        # Batch fetch model usages and delegations for selected rows
        session_ids = [r["id"] for r in rows]
        from collections import defaultdict
        usage_by_session = defaultdict(list)
        delegations_by_session = defaultdict(int)

        if session_ids:
            placeholders = ",".join(["?"] * len(session_ids))
            for db_path in self._get_all_state_dbs():
                conn = self._get_ro_conn_for_db(db_path)
                if not conn:
                    continue
                with conn:
                    cur = conn.cursor()
                    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                    if "session_model_usage" in tables:
                        try:
                            cur.execute(f"""
                                SELECT session_id, model, billing_provider, api_call_count, input_tokens, output_tokens, cache_read_tokens, reasoning_tokens, estimated_cost_usd
                                FROM session_model_usage
                                WHERE session_id IN ({placeholders})
                                ORDER BY input_tokens DESC
                            """, session_ids)
                            for ur in cur.fetchall():
                                usage_by_session[ur["session_id"]].append(dict(ur))
                        except Exception:
                            pass
                    if "async_delegations" in tables:
                        try:
                            cur.execute(f"""
                                SELECT origin_session, COUNT(*) as cnt
                                FROM async_delegations
                                WHERE origin_session IN ({placeholders})
                                GROUP BY origin_session
                            """, session_ids)
                            for dr in cur.fetchall():
                                delegations_by_session[dr["origin_session"]] += dr["cnt"]
                        except Exception:
                            pass

        meta_map = metadata_service.get_all_session_metadata_map()
        sessions = []
        for item in rows:
            sid = item.get("id")
            item["session_id"] = sid
            # Tag persona
            p_id = self.attribute_session_persona(item)
            if p_id and p_id in self.ROSTER_META:
                p_meta = self.ROSTER_META[p_id]
                item["persona_id"] = p_id
                item["persona_name"] = p_meta.get("name")
                item["persona_emoji"] = p_meta.get("emoji")
            else:
                item["persona_id"] = None
                item["persona_name"] = None
                item["persona_emoji"] = None

            item["is_subagent"] = item.get("source") == "subagent" or bool(item.get("parent_session_id"))
            # SPEC-HK-007 §3.5: ingress gateway attribution (cli | webui | telegram
            # | cron | pantheon_subagent) for session list badges & breakdowns.
            item["ingress_channel"] = classify_ingress_channel(
                item.get("source"), item.get("parent_session_id")
            )
            # SPEC-HK-007 §5.5: lightweight audit flags powering the session-row
            # MAP / GRAPH / THRASH badges. Reuses the incremental context-audit
            # cache (~1ms cold per session, dict hit warm); any trace failure
            # degrades to no badge and never breaks the list payload.
            try:
                _audit = self.compute_session_context_audit(sid)
                item["used_map_md"] = bool(_audit.get("used_map_md"))
                item["used_graphify"] = bool(_audit.get("used_graphify"))
                item["read_to_edit_ratio"] = float(_audit.get("read_to_edit_ratio") or 0.0)
            except Exception:
                item["used_map_md"] = False
                item["used_graphify"] = False
                item["read_to_edit_ratio"] = 0.0

            item["metadata"] = meta_map.get(sid, {
                "tags": [],
                "notes": "",
                "starred": 0,
                "tickets": [],
                "custom_name": None
            })
            item["models_used"] = usage_by_session.get(sid, [])
            if not item["models_used"] and item.get("model"):
                item["models_used"] = [{
                    "model": item.get("model"),
                    "billing_provider": item.get("billing_provider") or "",
                    "input_tokens": item.get("input_tokens") or 0,
                    "output_tokens": item.get("output_tokens") or 0,
                    "cache_read_tokens": item.get("cache_read_tokens") or 0,
                    "reasoning_tokens": item.get("reasoning_tokens") or 0,
                    "estimated_cost_usd": item.get("estimated_cost_usd") or 0.0,
                }]
            item["subagent_count"] = delegations_by_session.get(sid, 0)
            branch = item.get("git_branch") or ""
            detected_ticket = self._extract_ticket_from_branch(branch)
            if detected_ticket and not any(t["ticket_key"] == detected_ticket for t in item["metadata"].get("tickets", [])):
                item["detected_ticket"] = detected_ticket
            sessions.append(item)

        return {
            "total": total,
            "sessions": sessions,
            "limit": limit,
            "offset": offset,
            "all_models": [m["name"] for m in self.get_all_distinct_models()],
        }

    def _get_table_columns(self, cur: sqlite3.Cursor, table_name: str) -> List[str]:
        cur.execute(f"PRAGMA table_info('{table_name}')")
        return [col[1] for col in cur.fetchall()]

    def _extract_ticket_from_branch(self, branch: str) -> Optional[str]:
        if not branch:
            return None
        match = re.search(r'([A-Z]{2,10}-\d+|#\d+)', branch)
        return match.group(1) if match else None

    def get_session_by_id(self, session_id: str) -> Optional[Dict[str, Any]]:
        conn = self._find_db_for_session(session_id) or self._get_ro_conn()
        if not conn:
            return None

        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
            if not row:
                return None
            session = dict(row)
            session["session_id"] = session["id"]
            session["metadata"] = metadata_service.get_session_meta(session_id)

            # Fetch model usage breakdown
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "session_model_usage" in tables:
                cur.execute("SELECT * FROM session_model_usage WHERE session_id = ?", (session_id,))
                session["model_usages"] = [dict(r) for r in cur.fetchall()]
            else:
                session["model_usages"] = []

            # Fetch delegations/subagents
            if "async_delegations" in tables:
                cur.execute("""
                    SELECT * FROM async_delegations 
                    WHERE origin_session = ? OR parent_session_id = ? OR origin_session_id = ?
                """, (session_id, session_id, session_id))
                session["delegations"] = [dict(r) for r in cur.fetchall()]
            else:
                session["delegations"] = []

            # SPEC-HK-007: append the per-session 4-tier context audit. Missing or
            # malformed traces degrade to a schema-complete zero audit, never raise.
            # Internal aggregation keys (_path_waste, _compactions, ...) stay in the
            # reader; the API payload matches the blueprint SessionContextAudit schema.
            try:
                session["context_audit"] = {
                    k: v for k, v in self.compute_session_context_audit(session_id).items()
                    if not k.startswith("_")
                }
            except Exception:
                session["context_audit"] = self._empty_context_audit(
                    classify_ingress_channel(session.get("source"), session.get("parent_session_id"))
                )

            return session

    def get_session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        conn = self._find_db_for_session(session_id) or self._get_ro_conn()
        if not conn:
            return []

        with conn:
            cur = conn.cursor()
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "messages" not in tables:
                return []
            cur.execute("""
                SELECT * FROM messages 
                WHERE session_id = ? 
                ORDER BY timestamp ASC, id ASC
            """, (session_id,))
            rows = cur.fetchall()
            messages = []
            for r in rows:
                m = dict(r)
                # Some state.db rows store payloads (content/tool_calls/etc.) as
                # BLOBs; decode them so the detail endpoint stays JSON-serializable.
                for k, v in m.items():
                    if isinstance(v, (bytes, bytearray)):
                        try:
                            m[k] = bytes(v).decode("utf-8")
                        except UnicodeDecodeError:
                            m[k] = bytes(v).decode("utf-8", errors="replace")
                if m.get("tool_calls"):
                    try:
                        m["tool_calls_parsed"] = json.loads(m["tool_calls"])
                    except Exception:
                        m["tool_calls_parsed"] = None
                messages.append(m)
            return messages

    def get_session_timeline(self, session_id: str) -> List[Dict[str, Any]]:
        """Extract structured step-by-step timeline events for visual replay."""
        messages = self.get_session_messages(session_id)
        events = []
        step_index = 1

        for msg in messages:
            role = msg.get("role")
            ts = msg.get("timestamp")
            mid = msg.get("id")
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
            tool_calls = msg.get("tool_calls_parsed")
            tool_name = msg.get("tool_name")
            display_kind = msg.get("display_kind")

            # 1. If message has reasoning/thinking block
            if reasoning:
                events.append({
                    "id": f"step_{mid}_reasoning",
                    "step_number": step_index,
                    "type": "thought",
                    "role": role,
                    "timestamp": ts,
                    "title": "Reasoning & Plan",
                    "content": reasoning,
                    "message_id": mid,
                })
                step_index += 1

            # 2. If message is user input
            if role == "user":
                events.append({
                    "id": f"step_{mid}_user",
                    "step_number": step_index,
                    "type": "user_message",
                    "role": role,
                    "timestamp": ts,
                    "title": "User Message",
                    "content": content,
                    "message_id": mid,
                })
                step_index += 1

            # 3. If tool calls requested
            elif tool_calls and isinstance(tool_calls, list):
                for idx, tc in enumerate(tool_calls):
                    function_name = tc.get("function", {}).get("name", "tool")
                    args = tc.get("function", {}).get("arguments", "{}")
                    events.append({
                        "id": f"step_{mid}_call_{idx}",
                        "step_number": step_index,
                        "type": "tool_call",
                        "role": "assistant",
                        "timestamp": ts,
                        "title": f"Execute: {function_name}",
                        "tool_name": function_name,
                        "arguments": args,
                        "tool_call_id": tc.get("id"),
                        "message_id": mid,
                    })
                    step_index += 1

            # 4. If tool execution result
            elif role == "tool":
                is_error = "error" in content.lower() or "exception" in content.lower()
                events.append({
                    "id": f"step_{mid}_tool_result",
                    "step_number": step_index,
                    "type": "tool_result",
                    "role": role,
                    "timestamp": ts,
                    "title": f"Result: {tool_name or 'tool'}",
                    "tool_name": tool_name,
                    "tool_call_id": msg.get("tool_call_id"),
                    "content": content,
                    "status": "error" if is_error else "success",
                    "message_id": mid,
                })
                step_index += 1

            # 5. Assistant response content
            elif role == "assistant" and content:
                events.append({
                    "id": f"step_{mid}_assistant",
                    "step_number": step_index,
                    "type": "assistant_response",
                    "role": role,
                    "timestamp": ts,
                    "title": "Assistant Response",
                    "content": content,
                    "token_count": msg.get("token_count"),
                    "message_id": mid,
                })
                step_index += 1

        return events

    def get_session_subagents(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieve hierarchy of subagents and delegations for this session."""
        conn = self._find_db_for_session(session_id) or self._get_ro_conn()
        if not conn:
            return []

        with conn:
            cur = conn.cursor()
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "async_delegations" not in tables:
                return []
            cur.execute("""
                SELECT * FROM async_delegations 
                WHERE origin_session = ? OR parent_session_id = ? OR origin_session_id = ?
                ORDER BY dispatched_at ASC
            """, (session_id, session_id, session_id))
            delegations = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("task_json"):
                    try:
                        d["task_parsed"] = json.loads(d["task_json"])
                    except Exception:
                        d["task_parsed"] = d["task_json"]
                if d.get("result_json"):
                    try:
                        d["result_parsed"] = json.loads(d["result_json"])
                    except Exception:
                        d["result_parsed"] = d["result_json"]
                delegations.append(d)
            return delegations

    def get_analytics_overview(self, time_range: str = "30d") -> Dict[str, Any]:
        """Aggregate total tokens, costs, models, and tools with full multi-model and local vs cloud precision across all state databases."""
        import time
        from collections import defaultdict

        now_ts = time.time()
        cutoff_ts = None
        if time_range in ("today", "24h", "1d"):
            cutoff_ts = now_ts - 86400
        elif time_range in ("7d", "7days", "week"):
            cutoff_ts = now_ts - (7 * 86400)
        elif time_range in ("30d", "30days"):
            cutoff_ts = now_ts - (30 * 86400)
        elif time_range in ("month", "this_month"):
            import datetime
            now_dt = datetime.datetime.now()
            month_start = datetime.datetime(now_dt.year, now_dt.month, 1)
            cutoff_ts = month_start.timestamp()

        import datetime
        now_dt = datetime.datetime.now()
        month_start_ts = datetime.datetime(now_dt.year, now_dt.month, 1).timestamp()
        current_month_cost = 0.0

        total_sessions = 0
        total_messages = 0

        model_aggs: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "model": "",
            "session_ids": set(),
            "turns_by_session": {},
            "api_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
            "cost_usd": 0.0,
            "billing_provider": "",
        })

        source_counts: Dict[str, int] = defaultdict(int)
        tool_counts: Dict[str, int] = defaultdict(int)
        session_turn_counts: Dict[str, int] = {}
        daily_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "session_count": 0,
            "total_tokens": 0,
            "daily_cost": 0.0,
        })

        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            with conn:
                cur = conn.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

                # 1. Sessions summary and source distribution
                if "sessions" in tables:
                    if cutoff_ts is not None:
                        cur.execute("SELECT COUNT(*) as cnt, SUM(message_count) as msgs FROM sessions WHERE started_at >= ?", (cutoff_ts,))
                    else:
                        cur.execute("SELECT COUNT(*) as cnt, SUM(message_count) as msgs FROM sessions")
                    s_row = cur.fetchone()
                    if s_row and s_row["cnt"]:
                        total_sessions += (s_row["cnt"] or 0)
                        total_messages += (s_row["msgs"] or 0)

                    # Source platform distribution
                    cur.execute("SELECT COALESCE(source, 'cli') as source, COUNT(*) as cnt FROM sessions GROUP BY source")
                    for r in cur.fetchall():
                        source_counts[r["source"]] += r["cnt"]

                    # Per-session turn (message) counts — used to derive turns_count
                    # per model, since session_model_usage has no message_count column.
                    try:
                        if cutoff_ts is not None:
                            cur.execute("SELECT id, message_count FROM sessions WHERE started_at >= ?", (cutoff_ts,))
                        else:
                            cur.execute("SELECT id, message_count FROM sessions")
                        for r in cur.fetchall():
                            if r["id"]:
                                session_turn_counts[r["id"]] = (r["message_count"] or 0)
                    except Exception:
                        pass

                    # Daily activity (last 30 days)
                    cur.execute("""
                        SELECT 
                            strftime('%Y-%m-%d', datetime(started_at, 'unixepoch')) as date,
                            COUNT(*) as session_count,
                            SUM(input_tokens + output_tokens) as total_tokens,
                            SUM(estimated_cost_usd) as daily_cost
                        FROM sessions
                        WHERE started_at IS NOT NULL AND started_at > (strftime('%s', 'now') - 2592000)
                        GROUP BY date
                    """)
                    for r in cur.fetchall():
                        d_str = r["date"]
                        daily_stats[d_str]["session_count"] += (r["session_count"] or 0)
                        daily_stats[d_str]["total_tokens"] += (r["total_tokens"] or 0)
                        daily_stats[d_str]["daily_cost"] += (r["daily_cost"] or 0.0)

                # 2. Model usage breakdown
                has_usage = False
                if "session_model_usage" in tables:
                    if cutoff_ts is not None:
                        cur.execute("""
                            SELECT 
                                COALESCE(model, 'Unknown') as model,
                                session_id,
                                SUM(api_call_count) as api_call_count,
                                SUM(input_tokens) as input_tokens,
                                SUM(output_tokens) as output_tokens,
                                SUM(cache_read_tokens) as cache_read_tokens,
                                SUM(cache_write_tokens) as cache_write_tokens,
                                SUM(reasoning_tokens) as reasoning_tokens,
                                SUM(estimated_cost_usd) as cost_usd,
                                COALESCE(billing_provider, '') as billing_provider
                            FROM session_model_usage
                            WHERE (last_seen >= ? OR first_seen >= ?)
                            GROUP BY model, session_id
                        """, (cutoff_ts, cutoff_ts))
                    else:
                        cur.execute("""
                            SELECT 
                                COALESCE(model, 'Unknown') as model,
                                session_id,
                                SUM(api_call_count) as api_call_count,
                                SUM(input_tokens) as input_tokens,
                                SUM(output_tokens) as output_tokens,
                                SUM(cache_read_tokens) as cache_read_tokens,
                                SUM(cache_write_tokens) as cache_write_tokens,
                                SUM(reasoning_tokens) as reasoning_tokens,
                                SUM(estimated_cost_usd) as cost_usd,
                                COALESCE(billing_provider, '') as billing_provider
                            FROM session_model_usage
                            GROUP BY model, session_id
                        """)
                    u_rows = cur.fetchall()
                    if u_rows:
                        has_usage = True
                        for r in u_rows:
                            m_name = r["model"]
                            e = model_aggs[m_name]
                            e["model"] = m_name
                            if r["session_id"]:
                                e["session_ids"].add(r["session_id"])
                                # Aggregate turns from the parent session's message_count
                                e["turns_by_session"][r["session_id"]] = session_turn_counts.get(r["session_id"], 0)
                            e["api_call_count"] += (r["api_call_count"] or 0)
                            e["input_tokens"] += (r["input_tokens"] or 0)
                            e["output_tokens"] += (r["output_tokens"] or 0)
                            e["cache_read_tokens"] += (r["cache_read_tokens"] or 0)
                            e["cache_write_tokens"] += (r["cache_write_tokens"] or 0)
                            e["reasoning_tokens"] += (r["reasoning_tokens"] or 0)
                            e["cost_usd"] += (r["cost_usd"] or 0.0)
                            if r["billing_provider"] and not e["billing_provider"]:
                                e["billing_provider"] = r["billing_provider"]

                # Fallback to sessions table if session_model_usage was empty in this db
                if not has_usage and "sessions" in tables:
                    if cutoff_ts is not None:
                        cur.execute("""
                            SELECT 
                                COALESCE(model, 'Unknown') as model,
                                id as session_id,
                                SUM(api_call_count) as api_call_count,
                                SUM(input_tokens) as input_tokens,
                                SUM(output_tokens) as output_tokens,
                                SUM(cache_read_tokens) as cache_read_tokens,
                                SUM(cache_write_tokens) as cache_write_tokens,
                                SUM(reasoning_tokens) as reasoning_tokens,
                                SUM(estimated_cost_usd) as cost_usd,
                                COALESCE(billing_provider, '') as billing_provider
                            FROM sessions
                            WHERE started_at >= ?
                            GROUP BY model, id
                        """, (cutoff_ts,))
                    else:
                        cur.execute("""
                            SELECT 
                                COALESCE(model, 'Unknown') as model,
                                id as session_id,
                                SUM(api_call_count) as api_call_count,
                                SUM(input_tokens) as input_tokens,
                                SUM(output_tokens) as output_tokens,
                                SUM(cache_read_tokens) as cache_read_tokens,
                                SUM(cache_write_tokens) as cache_write_tokens,
                                SUM(reasoning_tokens) as reasoning_tokens,
                                SUM(estimated_cost_usd) as cost_usd,
                                COALESCE(billing_provider, '') as billing_provider
                            FROM sessions
                            GROUP BY model, id
                        """)
                    for r in cur.fetchall():
                        m_name = r["model"]
                        e = model_aggs[m_name]
                        e["model"] = m_name
                        if r["session_id"]:
                            e["session_ids"].add(r["session_id"])
                            e["turns_by_session"][r["session_id"]] = session_turn_counts.get(r["session_id"], 0)
                        e["api_call_count"] += (r["api_call_count"] or 0)
                        e["input_tokens"] += (r["input_tokens"] or 0)
                        e["output_tokens"] += (r["output_tokens"] or 0)
                        e["cache_read_tokens"] += (r["cache_read_tokens"] or 0)
                        e["cache_write_tokens"] += (r["cache_write_tokens"] or 0)
                        e["reasoning_tokens"] += (r["reasoning_tokens"] or 0)
                        e["cost_usd"] += (r["cost_usd"] or 0.0)
                        if r["billing_provider"] and not e["billing_provider"]:
                            e["billing_provider"] = r["billing_provider"]

                # 3. Tool usage distribution from messages
                if "messages" in tables:
                    try:
                        cur.execute("""
                            SELECT tool_name, COUNT(*) as count
                            FROM messages
                            WHERE tool_name IS NOT NULL AND tool_name != ''
                            GROUP BY tool_name
                        """)
                        for r in cur.fetchall():
                            tool_counts[r["tool_name"]] += r["count"]
                    except Exception:
                        pass

        # Annotate model rows with is_local, turns and readable provider tag
        model_rows = []
        for m_name, d in model_aggs.items():
            # turns_count = sum of message_count over the sessions that used this
            # model; fall back to api_call_count when session rows are unavailable.
            turns_count = sum(d["turns_by_session"].values())
            if not turns_count:
                turns_count = d["api_call_count"]
            tok_s, tier = model_throughput_profile(m_name)
            model_rows.append({
                "model": m_name,
                "session_count": len(d["session_ids"]),
                "api_call_count": d["api_call_count"],
                "turns_count": int(turns_count),
                "input_tokens": d["input_tokens"],
                "output_tokens": d["output_tokens"],
                "cache_read_tokens": d["cache_read_tokens"],
                "cache_write_tokens": d["cache_write_tokens"],
                "reasoning_tokens": d["reasoning_tokens"],
                "cost_usd": d["cost_usd"],
                "billing_provider": d["billing_provider"],
                "throughput_tok_per_sec": tok_s,
                "throughput_tier": tier,
            })
        model_rows.sort(key=lambda x: (x["input_tokens"] + x["output_tokens"]), reverse=True)

        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        total_reasoning = 0
        total_api_calls = 0
        total_cost = 0.0

        local_tokens = 0
        cloud_tokens = 0
        local_sessions = 0
        cloud_sessions = 0

        total_raw_cost = 0.0
        enhanced_models = []
        for m in model_rows:
            inp = m.get("input_tokens") or 0
            out = m.get("output_tokens") or 0
            cread = m.get("cache_read_tokens") or 0
            cwrite = m.get("cache_write_tokens") or 0
            reas = m.get("reasoning_tokens") or 0
            calls = m.get("api_call_count") or 0
            stored_cost = m.get("cost_usd") or 0.0
            scnt = m.get("session_count") or 0

            is_local = self._is_local_model(m.get("model", ""), m.get("billing_provider", ""))

            # Re-derive throughput now that the authoritative is_local flag is known,
            # so unrecognised models land on the correct cloud/local baseline.
            tok_s, tier = model_throughput_profile(m.get("model", ""), is_local=is_local)
            m["throughput_tok_per_sec"] = tok_s
            m["throughput_tier"] = tier
            
            # Reconcile via PricingEngine
            rec = pricing_engine.reconcile_usage(
                model_name=m.get("model", ""),
                input_tokens=inp,
                output_tokens=out,
                cache_read_tokens=cread,
                cache_write_tokens=cwrite,
                reasoning_tokens=reas,
                stored_cost_usd=stored_cost,
                billing_provider=m.get("billing_provider", ""),
            )
            cost = rec.cost_usd
            m["cost_usd"] = cost
            m["raw_cost_usd"] = rec.raw_stored_cost_usd
            m["is_reconciled"] = rec.is_reconciled
            m["is_local"] = is_local
            m["provider_category"] = self._classify_provider(m.get("model", ""))

            total_input += inp
            total_output += out
            total_cache_read += cread
            total_cache_write += cwrite
            total_reasoning += reas
            total_api_calls += calls
            total_cost += cost
            total_raw_cost += stored_cost

            if is_local:
                local_tokens += (inp + out)
                local_sessions += scnt
            else:
                cloud_tokens += (inp + out)
                cloud_sessions += scnt

            enhanced_models.append(m)

        # 3. Provider distribution
        provider_map: Dict[str, Dict[str, Any]] = {}
        for m in enhanced_models:
            p_name = m["provider_category"]
            if p_name not in provider_map:
                provider_map[p_name] = {
                    "provider": p_name,
                    "session_count": 0,
                    "api_call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cost_usd": 0.0,
                    "is_local": m["is_local"],
                }
            provider_map[p_name]["session_count"] += m.get("session_count", 0)
            provider_map[p_name]["api_call_count"] += m.get("api_call_count", 0)
            provider_map[p_name]["input_tokens"] += m.get("input_tokens", 0)
            provider_map[p_name]["output_tokens"] += m.get("output_tokens", 0)
            provider_map[p_name]["cache_read_tokens"] += m.get("cache_read_tokens", 0)
            provider_map[p_name]["cost_usd"] += m.get("cost_usd", 0.0)

        provider_distribution = sorted(
            list(provider_map.values()),
            key=lambda x: (x["input_tokens"] + x["output_tokens"]),
            reverse=True
        )

        # 4. Source platform distribution
        sources = [{"source": k, "count": v} for k, v in sorted(source_counts.items(), key=lambda x: x[1], reverse=True)]

        # 5. Tool usage distribution from messages
        tools = [{"tool_name": k, "count": v} for k, v in sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:30]]

        # 6. Daily activity (last 30 days)
        daily = [{"date": k, "session_count": v["session_count"], "total_tokens": v["total_tokens"], "daily_cost": v["daily_cost"]} for k, v in sorted(daily_stats.items(), key=lambda x: x[0])]

        # --- SPEC-HK-006: AI Studio Temporal Usage Data Contract ---
        bucket_type = "hour" if time_range in ("today", "24h", "1d") else "day"
        if bucket_type == "hour":
            sql_bucket = "strftime('%Y-%m-%d %H:00', datetime(last_seen, 'unixepoch', 'localtime'))"
        else:
            sql_bucket = "strftime('%Y-%m-%d', datetime(last_seen, 'unixepoch', 'localtime'))"

        temporal_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "input": defaultdict(int),
            "output": defaultdict(int),
            "cost": defaultdict(float),
            "reasoning": defaultdict(int)
        })
        temporal_models = set()

        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn: continue
            with conn:
                cur = conn.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "session_model_usage" in tables:
                    if cutoff_ts is not None:
                        cur.execute(f"""
                            SELECT 
                                {sql_bucket} as bucket,
                                COALESCE(model, 'Unknown') as model,
                                COALESCE(billing_provider, '') as billing_provider,
                                SUM(input_tokens) as inp,
                                SUM(output_tokens) as out,
                                SUM(cache_read_tokens) as cache_read,
                                SUM(cache_write_tokens) as cache_write,
                                SUM(reasoning_tokens) as reas,
                                SUM(estimated_cost_usd) as stored_cost
                            FROM session_model_usage
                            WHERE last_seen >= ?
                            GROUP BY bucket, model, billing_provider
                        """, (cutoff_ts,))
                    else:
                        cur.execute(f"""
                            SELECT 
                                {sql_bucket} as bucket,
                                COALESCE(model, 'Unknown') as model,
                                COALESCE(billing_provider, '') as billing_provider,
                                SUM(input_tokens) as inp,
                                SUM(output_tokens) as out,
                                SUM(cache_read_tokens) as cache_read,
                                SUM(cache_write_tokens) as cache_write,
                                SUM(reasoning_tokens) as reas,
                                SUM(estimated_cost_usd) as stored_cost
                            FROM session_model_usage
                            GROUP BY bucket, model, billing_provider
                        """)
                    for r in cur.fetchall():
                        b = r["bucket"]
                        if not b: continue
                        m = r["model"]
                        temporal_models.add(m)
                        
                        rec = pricing_engine.reconcile_usage(
                            model_name=m,
                            input_tokens=(r["inp"] or 0),
                            output_tokens=(r["out"] or 0),
                            cache_read_tokens=(r["cache_read"] or 0),
                            cache_write_tokens=(r["cache_write"] or 0),
                            reasoning_tokens=(r["reas"] or 0),
                            stored_cost_usd=(r["stored_cost"] or 0.0),
                            billing_provider=r["billing_provider"]
                        )
                        temporal_map[b]["input"][m] += (r["inp"] or 0)
                        temporal_map[b]["output"][m] += (r["out"] or 0)
                        temporal_map[b]["cost"][m] += rec.cost_usd

        # Create continuous timeline buckets
        import datetime
        temporal_buckets = []
        temporal_labels = []
        if bucket_type == "hour":
            start_dt = now_dt - datetime.timedelta(hours=24)
            start_dt = start_dt.replace(minute=0, second=0, microsecond=0)
            for i in range(25):
                dt = start_dt + datetime.timedelta(hours=i)
                temporal_buckets.append(dt.strftime("%Y-%m-%d %H:00"))
                temporal_labels.append(dt.strftime("%H:00"))
        elif time_range in ("month", "this_month"):
            start_dt = datetime.datetime(now_dt.year, now_dt.month, 1)
            days_in_month = (now_dt.date() - start_dt.date()).days
            for i in range(days_in_month + 1):
                dt = start_dt + datetime.timedelta(days=i)
                temporal_buckets.append(dt.strftime("%Y-%m-%d"))
                temporal_labels.append(dt.strftime("%b %d"))
        elif time_range in ("7d", "7days", "week"):
            start_dt = now_dt - datetime.timedelta(days=7)
            for i in range(8):
                dt = start_dt + datetime.timedelta(days=i)
                temporal_buckets.append(dt.strftime("%Y-%m-%d"))
                temporal_labels.append(dt.strftime("%b %d"))
        elif time_range == "all":
            valid_buckets = [b for b in temporal_map.keys() if len(b) == 10 and b.count("-") == 2]
            if valid_buckets:
                earliest_b = min(valid_buckets)
                try:
                    start_dt = datetime.datetime.strptime(earliest_b, "%Y-%m-%d")
                    days_span = min(180, max(30, (now_dt.date() - start_dt.date()).days))
                    start_dt = now_dt - datetime.timedelta(days=days_span)
                except Exception:
                    start_dt = now_dt - datetime.timedelta(days=30)
            else:
                start_dt = now_dt - datetime.timedelta(days=30)
            days = (now_dt.date() - start_dt.date()).days
            for i in range(days + 1):
                dt = start_dt + datetime.timedelta(days=i)
                temporal_buckets.append(dt.strftime("%Y-%m-%d"))
                temporal_labels.append(dt.strftime("%b %d"))
        else:
            days = 30
            start_dt = now_dt - datetime.timedelta(days=days)
            for i in range(days + 1):
                dt = start_dt + datetime.timedelta(days=i)
                temporal_buckets.append(dt.strftime("%Y-%m-%d"))
                temporal_labels.append(dt.strftime("%b %d"))

        series_inp = defaultdict(list)
        series_out = defaultdict(list)
        series_cost = defaultdict(list)
        series_total = defaultdict(list)
        sorted_temporal_models = sorted(list(temporal_models))

        for b in temporal_buckets:
            b_data = temporal_map.get(b, {})
            for m in sorted_temporal_models:
                i_val = b_data.get("input", {}).get(m, 0)
                o_val = b_data.get("output", {}).get(m, 0)
                c_val = b_data.get("cost", {}).get(m, 0.0)
                series_inp[m].append(i_val)
                series_out[m].append(o_val)
                series_cost[m].append(round(c_val, 4))
                series_total[m].append(i_val + o_val)

        temporal_usage = {
            "bucket_type": bucket_type,
            "time_range": time_range,
            "buckets": temporal_buckets,
            "labels": temporal_labels,
            "models": sorted_temporal_models,
            "series": {
                "input_tokens": dict(series_inp),
                "output_tokens": dict(series_out),
                "cost_usd": dict(series_cost),
                "total_tokens": dict(series_total)
            }
        }

        # KV Cache hit rate calculation
        total_prompt_processed = total_input + total_cache_read
        cache_hit_rate = round((total_cache_read / total_prompt_processed * 100), 2) if total_prompt_processed > 0 else 0.0
        zero_cost_ratio = round((local_tokens / (local_tokens + cloud_tokens) * 100), 1) if (local_tokens + cloud_tokens) > 0 else 0.0

        spend_cap = 250.0
        # If current_month_cost was not computed from a filtered view, compute it across models for month
        if current_month_cost == 0.0:
            for m in enhanced_models:
                if not m.get("is_local"):
                    current_month_cost += m.get("cost_usd", 0.0)

        spend_cap_pct = round(min(100.0, (current_month_cost / spend_cap) * 100), 1) if spend_cap > 0 else 0.0
        reconciled_delta = round(max(0.0, total_cost - total_raw_cost), 4)

        # 7. Inference velocity summary (SPEC-HK-005)
        live_chunkito = get_live_chunkito_throughput()
        live_dec = live_chunkito.get("decode_tok_s") if live_chunkito else None
        live_pref = live_chunkito.get("prefill_tok_s") if live_chunkito else None

        # Update resident APU model if live decode is measured
        if live_dec and live_dec > 0:
            for m in enhanced_models:
                m_low = (m.get("model") or "").lower()
                if "flash-next" in m_low or "qwen3.8-flash-next" in m_low:
                    m["throughput_tok_per_sec"] = live_dec

        fastest = max(
            enhanced_models,
            key=lambda x: (x.get("throughput_tok_per_sec", 0.0), x.get("model", "") == "gemini-3.8-flash"),
            default=None,
        ) if enhanced_models else None
        local_model_names = {m.get("model", "") for m in enhanced_models if m.get("is_local")}
        longest_ctx_local = (
            LONGEST_CONTEXT_LOCAL_MODEL if LONGEST_CONTEXT_LOCAL_MODEL in local_model_names
            else (max(sorted(local_model_names), key=lambda n: (":262k" in n.lower(), ":128k" in n.lower(), len(n)), default=None)
                  or LONGEST_CONTEXT_LOCAL_MODEL)
        )
        inference_velocity_summary = {
            "apu_decode_tok_per_sec": live_dec if (live_dec and live_dec > 0) else APU_DECODE_TOK_PER_SEC,
            "apu_prefill_tok_per_sec": live_pref if (live_pref and live_pref > 0) else APU_PREFILL_TOK_PER_SEC,
            "cloud_decode_tok_per_sec": CLOUD_DECODE_TOK_PER_SEC,
            "local_zero_cost_ratio_pct": zero_cost_ratio,
            "fastest_model": (fastest.get("model") if fastest else None) or "gemini-3.8-flash",
            "longest_context_local_model": longest_ctx_local,
            "is_live_telemetry": bool(live_chunkito and live_chunkito.get("is_live")),
        }

        return {
            "time_range": time_range,
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cache_read_tokens": total_cache_read,
            "total_cache_write_tokens": total_cache_write,
            "total_reasoning_tokens": total_reasoning,
            "total_api_calls": total_api_calls,
            "total_estimated_cost_usd": round(total_cost, 4),
            "total_actual_cost_usd": round(total_cost, 4),
            "raw_stored_cost_usd": round(total_raw_cost, 4),
            "reconciled_delta_usd": reconciled_delta,
            "current_month_cost_usd": round(current_month_cost, 2),
            "spend_cap_usd": spend_cap,
            "spend_cap_pct": spend_cap_pct,
            "local_sessions_count": local_sessions,
            "cloud_sessions_count": cloud_sessions,
            "local_tokens_total": local_tokens,
            "cloud_tokens_total": cloud_tokens,
            "local_zero_cost_ratio_pct": zero_cost_ratio,
            "cache_hit_rate_pct": cache_hit_rate,
            "temporal_usage": temporal_usage,
            "inference_velocity_summary": inference_velocity_summary,
            "model_distribution": enhanced_models,
            "provider_distribution": provider_distribution,
            "source_distribution": sources,
            "tool_distribution": tools,
            "daily_activity": daily,
        }

    # ------------------------------------------------------------------
    # SPEC-HK-007: per-session Context Tax audit engine
    # ------------------------------------------------------------------

    # In-memory incremental reader cache: session_id -> {_sig, audit}.
    # _sig = (db st_mtime_ns, db size, message rows for session, transcript_revision)
    # so an audit is only recomputed when the underlying session actually mutated.
    _CONTEXT_AUDIT_CACHE: Dict[str, Dict[str, Any]] = {}

    def invalidate_context_audit_cache(self) -> None:
        """Drop all cached per-session context audits (e.g. after a bulk import)."""
        self._CONTEXT_AUDIT_CACHE.clear()

    def _empty_context_audit(self, ingress_channel: str = "cli") -> Dict[str, Any]:
        """Schema-complete zero-value audit so missing data degrades gracefully."""
        return {
            "ingress_channel": ingress_channel,
            "used_map_md": False,
            "used_graphify": False,
            "turns_to_first_edit": None,
            "read_to_edit_ratio": 0.0,
            "cold_start_tokens": 0,
            "spatial_index_tokens": 0,
            "exploration_waste_tokens": 0,
            "productive_tokens": 0,
            "compaction_events": 0,
            "amnesia_reread_tokens": 0,
            "untouched_files_read": [],
            "is_coding_session": False,
            "total_prompt_tokens": 0,
            "_path_waste": {},
            "_edited_files": [],
            "_compactions": [],
        }

    def _hook_metadata(self, session_id: str) -> Dict[str, Any]:
        """Explicit live telemetry emitted by hermes_karma_hook.py (if any)."""
        try:
            target = KARMA_LIVE_DIR / f"{session_id}.json"
            if target.is_file():
                data = json.loads(target.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    _PATH_ARG_KEYS = (
        "path", "file_path", "filepath", "file", "target_file",
        "notebook_path", "abs_path", "filename",
    )

    @staticmethod
    def _extract_path_from_args(args: Any) -> Optional[str]:
        """Best-effort file path extraction from a tool call's argument payload."""
        if not args:
            return None
        parsed = args
        if isinstance(args, str):
            s = args.strip()
            try:
                parsed = json.loads(s)
            except Exception:
                m = re.search(r'"(?:path|file_path|filepath|file)"\s*:\s*"([^"]+)"', s)
                if m:
                    return m.group(1)
                m = re.search(r"(?:^|[\s'\(])(/[^\s'\",)\)]+)", s)
                return m.group(1) if m else None
        if isinstance(parsed, dict):
            for k in HermesReader._PATH_ARG_KEYS:
                v = parsed.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            for k in ("arguments", "input", "params"):
                if k in parsed:
                    p = HermesReader._extract_path_from_args(parsed[k])
                    if p:
                        return p
        return None

    def _locate_db_for_session(self, session_id: str) -> Optional[Path]:
        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            try:
                row = conn.cursor().execute(
                    "SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,)
                ).fetchone()
                if row:
                    return db_path
            except Exception:
                pass
            finally:
                conn.close()
        return None

    def compute_session_context_audit(self, session_id: str) -> Dict[str, Any]:
        """Compute (or reuse from the incremental mtime cache) the SPEC-HK-007
        context audit for a single session."""
        db_path = self._locate_db_for_session(session_id)
        if not db_path:
            return self._empty_context_audit()
        conn = self._get_ro_conn_for_db(db_path)
        if not conn:
            return self._empty_context_audit()
        try:
            with conn:
                cur = conn.cursor()
                st = db_path.stat()
                msg_count = cur.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
                ).fetchone()[0]
                rev = 0
                try:
                    rrow = cur.execute(
                        "SELECT transcript_revision FROM sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    rev = int(rrow[0] or 0) if rrow else 0
                except Exception:
                    pass
                sig = (st.st_mtime_ns, st.st_size, int(msg_count or 0), rev)
                cached = self._CONTEXT_AUDIT_CACHE.get(session_id)
                if cached and cached.get("_sig") == sig:
                    return dict(cached["audit"])
                audit = self._build_context_audit(conn, session_id)
                self._CONTEXT_AUDIT_CACHE[session_id] = {"_sig": sig, "audit": audit}
                return dict(audit)
        finally:
            conn.close()

    def _build_context_audit(self, conn: sqlite3.Connection, session_id: str) -> Dict[str, Any]:
        """Single-pass walk over a session's message trace producing the audit."""
        cur = conn.cursor()
        srow = cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not srow:
            return self._empty_context_audit()
        session = dict(srow)

        msg_cols = self._get_table_columns(cur, "messages")
        where = "session_id = ?"
        if "active" in msg_cols and "compacted" in msg_cols:
            where += " AND (active = 1 OR compacted = 1)"
        rows = cur.execute(
            f"SELECT * FROM messages WHERE {where} ORDER BY id ASC", (session_id,)
        ).fetchall()

        ingress = classify_ingress_channel(
            session.get("source"), session.get("parent_session_id")
        )
        hook_meta = self._hook_metadata(session_id)
        sys_prompt = session.get("system_prompt") or ""

        read_events: List[Dict[str, Any]] = []
        edits: List[tuple] = []          # (path, assistant tool-call turn)
        turn_index = 0                   # assistant tool-call turn counter
        turns_to_first_edit: Optional[int] = None
        used_map_md = any(mk in sys_prompt.lower() for mk in _SPATIAL_INDEX_FILE_MARKERS)
        used_graphify = False
        pending: Dict[str, Dict[str, Any]] = {}
        compactions: List[Dict[str, Any]] = []
        amnesia_event_ids: set = set()
        seen_context_tokens = 0
        first_user_prompt_tokens: Optional[int] = None

        for row in rows:
            m = dict(row)
            role = (m.get("role") or "").lower()
            content = m.get("content") or ""
            row_tokens = int(m.get("token_count") or 0) or estimate_tokens(content)
            seen_context_tokens += row_tokens

            # Compaction boundary: Hermes stores a compressed summary row.
            display_kind = str(m.get("display_kind") or "").lower()
            is_summary = (m.get("_compressed_summary") == 1) or display_kind in (
                "compaction", "context_compaction", "context_summary",
            )
            if is_summary:
                pre_tok = max(0, seen_context_tokens - row_tokens)
                post_tok = row_tokens
                compactions.append({
                    "pre_compaction_tokens": pre_tok,
                    "post_compaction_tokens": post_tok,
                    "tokens_reclaimed": max(0, pre_tok - post_tok),
                    "compression_ratio": round(1.0 - (post_tok / pre_tok), 4) if pre_tok > 0 else 0.0,
                    "turn": turn_index,
                    "pre_reads": {
                        e["path"] for e in read_events
                        if e.get("path") and e["turn"] <= turn_index
                    },
                })
                continue

            if role == "user" and first_user_prompt_tokens is None and content.strip():
                first_user_prompt_tokens = int(m.get("token_count") or 0) or estimate_tokens(content)

            if role == "assistant" and m.get("tool_calls"):
                try:
                    calls = json.loads(m["tool_calls"])
                except Exception:
                    calls = []
                if not isinstance(calls, list):
                    calls = []
                calls = [c for c in calls if isinstance(c, dict)]

                def _fn_of(call: Dict[str, Any]) -> Dict[str, Any]:
                    inner = call.get("function")
                    return inner if isinstance(inner, dict) else {}

                if any(_fn_of(c).get("name") or c.get("name") for c in calls):
                    turn_index += 1
                for tc in calls:
                    fn = _fn_of(tc)
                    name = str(fn.get("name") or tc.get("name") or "").strip().lower()
                    if not name:
                        continue
                    args = fn.get("arguments", tc.get("arguments"))
                    path = self._extract_path_from_args(args)
                    path_l = (path or "").lower()
                    try:
                        args_blob = args if isinstance(args, str) else json.dumps(args or {}, default=str)
                    except Exception:
                        args_blob = str(args)
                    args_blob = args_blob.lower()
                    is_spatial_file = any(mk in path_l for mk in _SPATIAL_INDEX_FILE_MARKERS)
                    is_graphify = (
                        ("graphify" in name)
                        or any(mk in path_l for mk in _GRAPHIFY_MARKERS)
                        or ("graphify" in args_blob and name in _READ_TOOL_NAMES)
                    )
                    if is_graphify:
                        used_graphify = True
                    if is_spatial_file and turn_index <= _SPATIAL_GUIDANCE_FIRST_TURNS:
                        used_map_md = True
                    if name in _EDIT_TOOL_NAMES and path:
                        edits.append((path, turn_index))
                        if turns_to_first_edit is None:
                            turns_to_first_edit = turn_index
                    elif name in _READ_TOOL_NAMES or is_graphify:
                        # graphify topology queries count as spatial reads (spec 3.2).
                        ev = {
                            "path": path,
                            "turn": turn_index,
                            "kind": "spatial" if (is_spatial_file or is_graphify) else "read",
                            "tokens": 0,
                            "failed": False,
                            "_id": tc.get("id") or f"ev_{len(read_events)}",
                        }
                        read_events.append(ev)
                        if tc.get("id"):
                            pending[tc["id"]] = ev

            elif role == "tool":
                tool_call_id = m.get("tool_call_id")
                ev = pending.pop(tool_call_id, None) if tool_call_id else None
                if ev is None:
                    # Fallback: most recent read event still without a result.
                    for cand in reversed(read_events):
                        if cand["tokens"] == 0:
                            ev = cand
                            break
                if ev is not None:
                    ev["tokens"] = max(ev["tokens"], row_tokens)
                    low = content[:300].lower()
                    if (
                        not content.strip()
                        or "no match" in low or "no matches" in low
                        or "no results" in low or "0 results" in low
                        or "not found" in low or low.startswith("error")
                    ):
                        ev["failed"] = True

        # ---- Tier 2: spatial index tokens ------------------------------------
        spatial_index_tokens = sum(e["tokens"] for e in read_events if e["kind"] == "spatial")

        # ---- Tier 3 companion: compaction Amnesia Tax -------------------------
        # Any read within K assistant turns after a compaction, targeting a file
        # already read before the compaction and not edited in between, is an
        # Amnesia Re-Read (spec 3.3).
        amnesia_reread_tokens = 0
        for comp in compactions:
            pre_reads = comp["pre_reads"]
            comp_turn = comp["turn"]
            for e in read_events:
                if e["kind"] != "read" or not e.get("path"):
                    continue
                if e["_id"] in amnesia_event_ids:
                    continue
                if e["turn"] <= comp_turn or (e["turn"] - comp_turn) > _AMNESIA_REREAD_WINDOW_TURNS:
                    continue
                if e["path"] not in pre_reads:
                    continue
                if any(p == e["path"] and comp_turn < t <= e["turn"] for p, t in edits):
                    continue
                amnesia_event_ids.add(e["_id"])
                amnesia_reread_tokens += e["tokens"]

        edited_paths = {p for p, _ in edits}
        is_coding_session = bool(edited_paths) or bool(hook_meta.get("files_edited"))
        distinct_reads_excl_map = len(
            {e["path"] for e in read_events if e.get("path") and e["kind"] != "spatial"}
        )
        read_to_edit_ratio = round(distinct_reads_excl_map / max(len(edited_paths), 1), 2)

        path_waste: Dict[str, Dict[str, Any]] = {}
        if is_coding_session:
            # Tier 3: reads of files that are never modified during the session.
            for e in read_events:
                if e["kind"] != "read" or not e.get("path") or e["path"] in edited_paths:
                    continue
                w = path_waste.setdefault(e["path"], {"count": 0, "tokens": 0})
                w["count"] += 1
                w["tokens"] += e["tokens"]
            exploration_waste_tokens = sum(w["tokens"] for w in path_waste.values())
        else:
            # Pure Q&A / research exemption (spec 3.1 Tier 3 exclusion): primary
            # target reads are productive; only redundant re-reads and failed or
            # empty search sweeps count as Exploration Burn.
            exploration_waste_tokens = 0
            seen_reads: set = set()
            for e in read_events:
                if e["kind"] != "read" or not e.get("path"):
                    continue
                if (e["path"] in seen_reads) or e["failed"]:
                    w = path_waste.setdefault(e["path"], {"count": 0, "tokens": 0})
                    w["count"] += 1
                    w["tokens"] += e["tokens"]
                    exploration_waste_tokens += e["tokens"]
                else:
                    seen_reads.add(e["path"])

        # ---- Tier 1: cold start / base harness tax ----------------------------
        cold_start_tokens = hook_meta.get("cold_start_tokens")
        if cold_start_tokens is None:
            turn1 = hook_meta.get("turn1_prompt_tokens")
            if turn1 is not None and first_user_prompt_tokens is not None:
                cold_start_tokens = max(0, int(turn1) - first_user_prompt_tokens)
            else:
                cold_start_tokens = estimate_tokens(sys_prompt)

        total_prompt_tokens = int(session.get("input_tokens") or 0)
        t1, t2, t3, t4 = reconcile_context_buckets(
            total_prompt_tokens, cold_start_tokens, spatial_index_tokens, exploration_waste_tokens
        )

        untouched = sorted(
            {
                e["path"] for e in read_events
                if e.get("path") and e["kind"] != "spatial" and e["path"] not in edited_paths
            }
        )

        return {
            "ingress_channel": ingress,
            "used_map_md": used_map_md,
            "used_graphify": used_graphify,
            "turns_to_first_edit": turns_to_first_edit,
            "read_to_edit_ratio": read_to_edit_ratio,
            "cold_start_tokens": t1,
            "spatial_index_tokens": t2,
            "exploration_waste_tokens": t3,
            "productive_tokens": t4,
            "compaction_events": len(compactions),
            "amnesia_reread_tokens": amnesia_reread_tokens,
            "untouched_files_read": untouched,
            "is_coding_session": is_coding_session,
            "total_prompt_tokens": total_prompt_tokens,
            # Internal aggregation keys (stripped before API serialization by callers):
            "_path_waste": path_waste,
            "_edited_files": sorted(edited_paths),
            "_compactions": [
                {k: v for k, v in c.items() if k != "pre_reads"} for c in compactions
            ],
        }

    # ------------------------------------------------------------------
    # SPEC-HK-007: aggregated Context Tax / Spatial ROI analytics
    # ------------------------------------------------------------------

    def _resolve_map_file(self) -> Optional[Path]:
        env_path = os.getenv("HERMES_KARMA_MAP_PATH")
        candidates = []
        if env_path:
            candidates.append(Path(os.path.expanduser(env_path)))
        candidates.extend([
            Path.cwd() / "docs" / "MAP.md",
            HERMES_DIR / "docs" / "MAP.md",
        ])
        for c in candidates:
            try:
                if c.is_file():
                    return c
            except Exception:
                continue
        return None

    def _load_map_content(self) -> tuple:
        f = self._resolve_map_file()
        if not f:
            return None, ""
        try:
            return f, f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return f, ""

    @staticmethod
    def _path_in_map(path: str, map_content: str) -> bool:
        if not path or not map_content:
            return False
        mc = map_content.lower()
        p = path.lower()
        if p in mc:
            return True
        base = p.rsplit("/", 1)[-1]
        return bool(base) and base in mc

    def _compute_map_drift(self, map_file: Optional[Path] = None,
                           map_content: str = "",
                           unindexed_candidates: Optional[List[str]] = None) -> Dict[str, Any]:
        """Map Staleness / Drift detector (spec 3.2C.2).

        Degrades gracefully to ``is_stale: false, commits_since_map_update: 0``
        when docs/MAP.md is absent or the repo has no git history (blueprint §3.3).
        """
        drift: Dict[str, Any] = {
            "is_stale": False,
            "map_last_updated_iso": None,
            "commits_since_map_update": 0,
            "unindexed_files": [],
        }
        if map_file is None:
            map_file, map_content = self._load_map_content()
        if not map_file:
            return drift
        try:
            import subprocess
            map_ts = None
            root_r = subprocess.run(
                ["git", "-C", str(map_file.parent), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if root_r.returncode == 0:
                root = root_r.stdout.strip()
                try:
                    rel = map_file.resolve().relative_to(Path(root).resolve()).as_posix()
                except Exception:
                    rel = str(map_file)
                ct = subprocess.run(
                    ["git", "-C", root, "log", "-1", "--format=%ct", "--", rel],
                    capture_output=True, text=True, timeout=5,
                )
                sha = subprocess.run(
                    ["git", "-C", root, "log", "-1", "--format=%H", "--", rel],
                    capture_output=True, text=True, timeout=5,
                )
                out = ct.stdout.strip()
                if out.isdigit():
                    map_ts = int(out)
                    sha_out = sha.stdout.strip()
                    if sha_out:
                        cnt = subprocess.run(
                            ["git", "-C", root, "rev-list", "--count", f"{sha_out}..HEAD"],
                            capture_output=True, text=True, timeout=5,
                        )
                        if cnt.returncode == 0 and cnt.stdout.strip().isdigit():
                            drift["commits_since_map_update"] = int(cnt.stdout.strip())
            if map_ts is None:
                # Map never committed: fall back to filesystem mtime.
                map_ts = int(map_file.stat().st_mtime)
                cnt = subprocess.run(
                    ["git", "-C", str(map_file.parent), "rev-list", "--count",
                     f"--after=@{map_ts}", "HEAD"],
                    capture_output=True, text=True, timeout=5,
                )
                if cnt.returncode == 0 and cnt.stdout.strip().isdigit():
                    drift["commits_since_map_update"] = int(cnt.stdout.strip())
            import datetime
            drift["map_last_updated_iso"] = datetime.datetime.fromtimestamp(
                map_ts, datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
        drift["is_stale"] = drift["commits_since_map_update"] > _MAP_DRIFT_COMMIT_THRESHOLD
        try:
            drift["unindexed_files"] = [
                p for p in (unindexed_candidates or [])
                if not self._path_in_map(p, map_content)
            ][:_TOP_TRASH_LIMIT]
        except Exception:
            drift["unindexed_files"] = []
        return drift

    def _dominant_cloud_model(self) -> Optional[str]:
        """Cloud (billable) model with the most input tokens across all state dbs."""
        best_model, best_tokens = None, -1
        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            try:
                with conn:
                    cur = conn.cursor()
                    tables = [r[0] for r in cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()]
                    if "session_model_usage" not in tables:
                        continue
                    for r in cur.execute(
                        "SELECT model, COALESCE(SUM(input_tokens), 0) AS t "
                        "FROM session_model_usage GROUP BY model"
                    ).fetchall():
                        m = r[0] or ""
                        if not m or self._is_local_model(m, ""):
                            continue
                        t = int(r["t"] or 0)
                        if t > best_tokens:
                            best_tokens, best_model = t, m
            except Exception:
                pass
            finally:
                conn.close()
        return best_model

    def _counterfactual_input_token_usd(self, tokens: int) -> float:
        """Price N counterfactual input tokens at the dominant cloud model's rate."""
        if tokens <= 0:
            return 0.0
        model = self._dominant_cloud_model()
        if model:
            try:
                cost = pricing_engine.calculate_cost(
                    model_name=model, input_tokens=tokens, output_tokens=0
                )
                if cost and cost > 0:
                    return float(cost)
            except Exception:
                pass
        return round(_DEFAULT_INPUT_USD_PER_MILLION * tokens / 1_000_000.0, 6)

    def get_context_tax_analytics(self, days: int = 30) -> Dict[str, Any]:
        """SPEC-HK-007: aggregate 4-tier Context Tax, Spatial Navigation ROI,
        compaction/amnesia stats, Top Thrash Files and ingress attribution over
        a rolling window. Per-session audits ride the incremental mtime cache."""
        days = max(1, int(days or 30))
        cutoff_ts = time.time() - days * 86400

        breakdown = {
            "cold_start_tokens": 0,
            "spatial_index_tokens": 0,
            "exploration_waste_tokens": 0,
            "productive_tokens": 0,
            "amnesia_reread_tokens": 0,
        }
        mapped = {"sessions": 0, "ttfe": [], "ratio": [], "waste": [], "spatial": []}
        unmapped = {"sessions": 0, "ttfe": [], "ratio": [], "waste": []}
        ingress: Dict[str, Dict[str, int]] = {
            ch: {"sessions": 0, "total_tokens": 0} for ch in INGRESS_CHANNELS
        }
        compaction_events = 0
        compression_ratios: List[float] = []
        tokens_reclaimed_total = 0
        path_waste_global: Dict[str, Dict[str, int]] = {}
        edited_files_window: set = set()
        audited_sessions = 0
        cap_reached = False

        for db_path in self._get_all_state_dbs():
            if cap_reached:
                break
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            try:
                with conn:
                    cur = conn.cursor()
                    tables = [r[0] for r in cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()]
                    if "sessions" not in tables or "messages" not in tables:
                        continue
                    s_cols = self._get_table_columns(cur, "sessions")
                    hidden_clause = "AND hidden = 0" if "hidden" in s_cols else ""
                    rows = cur.execute(
                        f"SELECT id, input_tokens, output_tokens FROM sessions "
                        f"WHERE started_at >= ? {hidden_clause} ORDER BY started_at DESC",
                        (cutoff_ts,),
                    ).fetchall()
                session_rows = [dict(r) for r in rows]
            except Exception:
                session_rows = []
            finally:
                conn.close()

            for s in session_rows:
                sid = s.get("id")
                if not sid:
                    continue
                if audited_sessions >= _MAX_AUDITED_SESSIONS:
                    cap_reached = True
                    break
                audited_sessions += 1
                try:
                    audit = self.compute_session_context_audit(sid)
                except Exception:
                    continue

                for k in ("cold_start_tokens", "spatial_index_tokens",
                          "exploration_waste_tokens", "productive_tokens",
                          "amnesia_reread_tokens"):
                    breakdown[k] += int(audit.get(k) or 0)

                ch = audit.get("ingress_channel") or "cli"
                bucket = ingress.setdefault(ch, {"sessions": 0, "total_tokens": 0})
                bucket["sessions"] += 1
                bucket["total_tokens"] += int(s.get("input_tokens") or 0) + int(s.get("output_tokens") or 0)

                if audit.get("is_coding_session"):
                    e_w = int(audit.get("exploration_waste_tokens") or 0)
                    s_tok = int(audit.get("spatial_index_tokens") or 0)
                    if audit.get("used_map_md") or audit.get("used_graphify"):
                        mapped["sessions"] += 1
                        mapped["waste"].append(e_w)
                        mapped["spatial"].append(s_tok)
                        if audit.get("turns_to_first_edit") is not None:
                            mapped["ttfe"].append(float(audit["turns_to_first_edit"]))
                        mapped["ratio"].append(float(audit.get("read_to_edit_ratio") or 0.0))
                    else:
                        unmapped["sessions"] += 1
                        unmapped["waste"].append(e_w)
                        if audit.get("turns_to_first_edit") is not None:
                            unmapped["ttfe"].append(float(audit["turns_to_first_edit"]))
                        unmapped["ratio"].append(float(audit.get("read_to_edit_ratio") or 0.0))

                for p, w in (audit.get("_path_waste") or {}).items():
                    g = path_waste_global.setdefault(p, {"count": 0, "tokens": 0})
                    g["count"] += int(w.get("count") or 0)
                    g["tokens"] += int(w.get("tokens") or 0)
                for p in (audit.get("_edited_files") or []):
                    edited_files_window.add(p)
                for ev in (audit.get("_compactions") or []):
                    compaction_events += 1
                    tokens_reclaimed_total += int(ev.get("tokens_reclaimed") or 0)
                    if ev.get("compression_ratio") is not None:
                        compression_ratios.append(float(ev["compression_ratio"]))

        # Counterfactual exploration savings (spec 3.2B): per mapped session,
        # Net = (E_unmapped_avg - E_session) - S_session, summed across the window.
        e_unmapped_avg = (
            (sum(unmapped["waste"]) / len(unmapped["waste"])) if unmapped["waste"] else 0.0
        )
        net_saved = 0.0
        for e_m, s_m in zip(mapped["waste"], mapped["spatial"]):
            net_saved += (e_unmapped_avg - e_m) - s_m
        net_saved = int(round(net_saved))
        spatial_savings_usd = self._counterfactual_input_token_usd(max(0, net_saved))

        map_file, map_content = self._load_map_content()
        top_thrash_files = [
            {
                "path": p,
                "unedited_read_count": w["count"],
                "wasted_tokens": w["tokens"],
                "in_map_md": self._path_in_map(p, map_content),
            }
            for p, w in sorted(
                path_waste_global.items(), key=lambda kv: kv[1]["tokens"], reverse=True
            )[:_TOP_TRASH_LIMIT]
        ]

        def _avg(xs: List[float]) -> float:
            return round(sum(xs) / len(xs), 2) if xs else 0.0

        return {
            "window_days": days,
            "token_breakdown": breakdown,
            "spatial_roi": {
                "mapped_sessions_count": mapped["sessions"],
                "unmapped_sessions_count": unmapped["sessions"],
                "avg_turns_to_first_edit_mapped": _avg(mapped["ttfe"]),
                "avg_turns_to_first_edit_unmapped": _avg(unmapped["ttfe"]),
                "avg_read_to_edit_ratio_mapped": _avg(mapped["ratio"]),
                "avg_read_to_edit_ratio_unmapped": _avg(unmapped["ratio"]),
                "net_exploration_tokens_saved": net_saved,
                "counterfactual_spatial_savings_usd": round(spatial_savings_usd, 4),
                "map_drift": self._compute_map_drift(
                    map_file=map_file,
                    map_content=map_content,
                    unindexed_candidates=sorted(edited_files_window),
                ),
            },
            "compaction_stats": {
                "total_compactions": compaction_events,
                "avg_compression_ratio": (
                    round(sum(compression_ratios) / len(compression_ratios), 4)
                    if compression_ratios else 0.0
                ),
                "total_tokens_reclaimed": tokens_reclaimed_total,
                "total_amnesia_reread_tokens": breakdown["amnesia_reread_tokens"],
            },
            "top_thrash_files": top_thrash_files,
            "ingress_breakdown": ingress,
        }

    def get_skills_catalog(self) -> List[Dict[str, Any]]:
        """List all autonomous and custom skills in ~/.hermes/skills."""
        skills = []
        if not HERMES_SKILLS_DIR.exists():
            return skills

        for skill_path in HERMES_SKILLS_DIR.rglob("SKILL.md"):
            try:
                content = skill_path.read_text(encoding="utf-8")
                # Parse frontmatter
                meta = {}
                body = content
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        try:
                            meta = yaml.safe_load(parts[1]) or {}
                            body = parts[2].strip()
                        except Exception:
                            pass

                rel_path = skill_path.relative_to(HERMES_SKILLS_DIR)
                category = str(rel_path.parent) if rel_path.parent != Path(".") else "general"
                name = meta.get("name") or skill_path.parent.name
                desc = meta.get("description") or ""

                skills.append({
                    "name": name,
                    "category": category,
                    "description": desc,
                    "version": meta.get("version", "1.0.0"),
                    "path": str(skill_path),
                    "relative_path": str(rel_path),
                    "tags": meta.get("metadata", {}).get("hermes", {}).get("tags", []),
                    "content": content,
                    "body_preview": body[:300] + "..." if len(body) > 300 else body,
                    "mtime": skill_path.stat().st_mtime,
                })
            except Exception as e:
                continue

        skills.sort(key=lambda x: x["name"])
        return skills

    # ------------------------------------------------------------------
    # SPEC-HK-007 §3.4: Skill ROI & "Dead Skill" auditor
    # ------------------------------------------------------------------

    _SKILL_ROI_CACHE: Dict[str, Any] = {}

    def invalidate_skill_roi_cache(self) -> None:
        """Drop the cached Skill ROI audit (e.g. after installing/removing skills)."""
        self._SKILL_ROI_CACHE.clear()

    @staticmethod
    def _match_skill_in_blob(blob_l: str, tool_name_l: str,
                             aliases: List[str],
                             alias_to_name: Dict[str, str]) -> Optional[str]:
        """Attribute one tool call to an installed skill.

        * Explicit skill tools (``skill_view`` / ``skill_manage`` ...) match on
          a skill alias appearing anywhere in the call arguments.
        * Read/search tools match only when the arguments reference the skill's
          ``SKILL.md`` path (e.g. ``.../skills/<category>/<name>/SKILL.md``).
        """
        is_skill_tool = (tool_name_l in _SKILL_VIEW_TOOL_NAMES
                         or tool_name_l in _SKILL_MANAGE_TOOL_NAMES)
        is_read_tool = tool_name_l in _READ_TOOL_NAMES
        if not (is_skill_tool or is_read_tool):
            return None
        if is_read_tool and "skill.md" not in blob_l:
            return None
        for alias in aliases:  # longest aliases first
            if alias and alias in blob_l:
                return alias_to_name.get(alias)
        return None

    def get_skill_roi_audit(self, days: int = 30) -> Dict[str, Any]:
        """SPEC-HK-007 §3.4: Skill ROI & Dead Skill auditor.

        For every installed SKILL.md: static token footprint (router
        description + expanded body), invocation count over a rolling window,
        and tool-call failure correlation across sessions that invoked the
        skill (``failed_tool_calls / total_tool_calls_in_skill_session``).
        Skills with 0 invocations in the window are flagged dormant/dead while
        still paying router description tokens in every base prompt.

        The whole-window scan is a single pass over session message traces and
        is TTL-cached in-process to keep repeated API calls fast.
        """
        now = time.time()
        if (self._SKILL_ROI_CACHE
                and (now - float(self._SKILL_ROI_CACHE.get("computed_at") or 0))
                < _SKILL_ROI_TTL_SECONDS):
            return dict(self._SKILL_ROI_CACHE["audit"])

        days = max(1, int(days or 30))
        cutoff_ts = now - days * 86400

        catalog = self.get_skills_catalog()
        stats: Dict[str, Dict[str, Any]] = {}
        alias_to_name: Dict[str, str] = {}
        for sk in catalog:
            name = sk["name"]
            content = sk.get("content") or ""
            body = content
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    body = parts[2]
            desc_tokens = estimate_tokens(sk.get("description") or "")
            body_tokens = estimate_tokens(body)
            stats[name] = {
                "name": name,
                "category": sk.get("category"),
                "router_description_tokens": desc_tokens,
                "body_tokens": body_tokens,
                "skill_token_weight": desc_tokens + body_tokens,
                "invocations": 0,
                "invoking_sessions": set(),
                "last_invoked_at": None,
                "skill_session_total_tool_calls": 0,
                "skill_session_failed_tool_calls": 0,
            }
            sk_dir = str(Path(sk.get("relative_path") or "").parent).lower()
            if sk_dir and sk_dir != ".":
                alias_to_name.setdefault(sk_dir, name)
            alias_to_name.setdefault(name.lower(), name)
        aliases_sorted = sorted(alias_to_name.keys(), key=len, reverse=True)

        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            try:
                with conn:
                    cur = conn.cursor()
                    tables = [r[0] for r in cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()]
                    if "sessions" not in tables or "messages" not in tables:
                        continue
                    s_cols = self._get_table_columns(cur, "sessions")
                    hidden_clause = "AND hidden = 0" if "hidden" in s_cols else ""
                    rows = cur.execute(
                        f"SELECT id, started_at FROM sessions "
                        f"WHERE started_at >= ? {hidden_clause} "
                        f"ORDER BY started_at DESC",
                        (cutoff_ts,),
                    ).fetchall()
                    session_rows = [dict(r) for r in rows][:_MAX_AUDITED_SESSIONS]

                    msg_cols = self._get_table_columns(cur, "messages")
                    msg_where = "session_id = ?"
                    if "active" in msg_cols and "compacted" in msg_cols:
                        msg_where += " AND (active = 1 OR compacted = 1)"

                    for srow in session_rows:
                        sid = srow.get("id")
                        started_at = srow.get("started_at")
                        if not sid:
                            continue
                        calls_in_session = 0
                        failed_in_session = 0
                        invoked_here: set = set()
                        for mrow in cur.execute(
                            f"SELECT role, content, tool_calls FROM messages "
                            f"WHERE {msg_where} ORDER BY id ASC",
                            (sid,),
                        ).fetchall():
                            m = dict(mrow)
                            role = (m.get("role") or "").lower()
                            if role == "assistant" and m.get("tool_calls"):
                                try:
                                    calls = json.loads(m["tool_calls"])
                                except Exception:
                                    calls = []
                                if not isinstance(calls, list):
                                    calls = []
                                for tc in calls:
                                    if not isinstance(tc, dict):
                                        continue
                                    inner = tc.get("function")
                                    inner = inner if isinstance(inner, dict) else {}
                                    tname = str(
                                        inner.get("name") or tc.get("name") or ""
                                    ).strip().lower()
                                    if not tname:
                                        continue
                                    calls_in_session += 1
                                    args = inner.get("arguments", tc.get("arguments"))
                                    try:
                                        blob = args if isinstance(args, str) else json.dumps(
                                            args or {}, default=str
                                        )
                                    except Exception:
                                        blob = str(args)
                                    matched = self._match_skill_in_blob(
                                        blob.lower(), tname, aliases_sorted, alias_to_name
                                    )
                                    if matched and matched in stats:
                                        stats[matched]["invocations"] += 1
                                        invoked_here.add(matched)
                                        cur_ts = float(started_at or 0)
                                        last = stats[matched]["last_invoked_at"]
                                        if last is None or cur_ts > last:
                                            stats[matched]["last_invoked_at"] = cur_ts
                            elif role == "tool" and _tool_output_is_failure(
                                str(m.get("content") or "")
                            ):
                                failed_in_session += 1

                        for sk_name in invoked_here:
                            st = stats[sk_name]
                            st["invoking_sessions"].add(sid)
                            st["skill_session_total_tool_calls"] += calls_in_session
                            st["skill_session_failed_tool_calls"] += failed_in_session
            except Exception:
                pass
            finally:
                conn.close()

        skills_out: List[Dict[str, Any]] = []
        dead_skills: List[str] = []
        for st in stats.values():
            total_calls = int(st["skill_session_total_tool_calls"])
            failed_calls = int(st["skill_session_failed_tool_calls"])
            dormant_dead = int(st["invocations"]) == 0
            if dormant_dead:
                dead_skills.append(st["name"])
            skills_out.append({
                k: (len(v) if isinstance(v, set) else v) for k, v in st.items()
            } | {
                "skill_error_rate": (
                    round(failed_calls / total_calls, 4) if total_calls else 0.0
                ),
                "is_dormant_dead": dormant_dead,
            })
        skills_out.sort(key=lambda x: (-int(x["skill_token_weight"]), x["name"]))

        audit = {
            "window_days": days,
            "totals": {
                "installed_skills": len(skills_out),
                "total_static_footprint_tokens": sum(
                    s["skill_token_weight"] for s in skills_out
                ),
                "total_router_description_tokens": sum(
                    s["router_description_tokens"] for s in skills_out
                ),
                "dormant_dead_count": len(dead_skills),
                "dormant_router_tax_tokens": sum(
                    s["router_description_tokens"] for s in skills_out
                    if s["is_dormant_dead"]
                ),
            },
            "dead_skills": sorted(dead_skills),
            "skills": skills_out,
        }
        self._SKILL_ROI_CACHE = {"computed_at": now, "audit": audit}
        return dict(audit)

    def get_skill_history(self, skill_name: str) -> Dict[str, Any]:
        """Fetch skill detail, snapshots, and diffs."""
        catalog = self.get_skills_catalog()
        matched = next((s for s in catalog if s["name"] == skill_name), None)
        if not matched:
            return {"error": "Skill not found"}

        return {
            "skill": matched,
            "history": [
                {
                    "version": matched.get("version", "1.0.0"),
                    "timestamp": matched.get("mtime"),
                    "change_summary": "Current active version in ~/.hermes/skills/",
                    "content": matched.get("content"),
                }
            ]
        }

    def get_memory_state(self) -> Dict[str, Any]:
        """Read current MEMORY.md and USER.md."""
        memory_content = ""
        user_content = ""

        if HERMES_MEMORY_FILE.exists():
            memory_content = HERMES_MEMORY_FILE.read_text(encoding="utf-8")
        if HERMES_USER_FILE.exists():
            user_content = HERMES_USER_FILE.read_text(encoding="utf-8")

        # Parse bullet / paragraph blocks
        memory_items = [
            line.strip() for line in memory_content.split("§") if line.strip()
        ] if "§" in memory_content else [l for l in memory_content.splitlines() if l.strip()]

        user_items = [
            line.strip() for line in user_content.split("§") if line.strip()
        ] if "§" in user_content else [l for l in user_content.splitlines() if l.strip()]

        return {
            "memory": {
                "raw": memory_content,
                "items": memory_items,
                "character_count": len(memory_content),
                "mtime": HERMES_MEMORY_FILE.stat().st_mtime if HERMES_MEMORY_FILE.exists() else None,
            },
            "user": {
                "raw": user_content,
                "items": user_items,
                "character_count": len(user_content),
                "mtime": HERMES_USER_FILE.stat().st_mtime if HERMES_USER_FILE.exists() else None,
            }
        }

    def get_cron_jobs(self) -> List[Dict[str, Any]]:
        """Inspect scheduled jobs in ~/.hermes/cron/ or state."""
        jobs = []
        if not HERMES_CRON_DIR.exists():
            return jobs

        for f in HERMES_CRON_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["file"] = f.name
                jobs.append(data)
            except Exception:
                continue

        # Also scan state_meta or cron configs if present in DB
        conn = self._get_ro_conn()
        if conn:
            with conn:
                cur = conn.cursor()
                try:
                    cur.execute("SELECT * FROM state_meta WHERE key LIKE 'cron_%'")
                    for r in cur.fetchall():
                        jobs.append({"key": r[0], "value": r[1]})
                except Exception:
                    pass

        return jobs

    ROSTER_META = {
        "owl": {
            "name": "Owl",
            "emoji": "🦉",
            "role": "Chief Architect & Master Reasoning",
            "domain": "Architecture blueprints, trade-off arbitration, failure domains, ADRs, Mermaid graphs",
            "default_model": "gemini-3.7-flash",
            "accent_color": "emerald",
        },
        "rabbit": {
            "name": "Rabbit",
            "emoji": "🐰",
            "role": "Taskmaster & Kanban Coordinator",
            "domain": "Decomposing specs into atomic Kanban tasks, dependency chains, sprint cadence",
            "default_model": "hf.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF:UD-IQ2_M",
            "accent_color": "brand",
        },
        "tigger": {
            "name": "Tigger",
            "emoji": "🐯",
            "role": "DevOps & Systems Executor (Local Swarm)",
            "domain": "High-speed test-driven implementation, boilerplate scaffolding, code generation (12 slots w/ native 256k on chunkito)",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "amber",
        },
        "jagular": {
            "name": "Jagular",
            "emoji": "🐆",
            "role": "The Beast & Big Iron Forensics (Sole Hunter)",
            "domain": "Monster-context whole-repo architectural forensics (>50k–262k tokens, Qwen3.8-Flash-Next 177B on chunkito; requires mutual exclusion vacate protocol)",
            "default_model": "qwen3.8-flash-next:262k",
            "accent_color": "orange",
        },
        "piglet": {
            "name": "Piglet",
            "emoji": "🐷",
            "role": "Agile Scout & Log Triager",
            "domain": "Rapid log sniffing, tracebacks triage, lightweight classifications, micro-summaries",
            "default_model": "qwen3.5:latest",
            "accent_color": "rose",
        },
        "eeyore": {
            "name": "Eeyore",
            "emoji": "🫏",
            "role": "Safety Officer & Adversarial Auditor",
            "domain": "Pre-commit security audits, permission leakage, secret exposure, adversarial failure modes",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "indigo",
        },
        "pooh": {
            "name": "Pooh",
            "emoji": "🐻",
            "role": "Knowledge Gardener & Vault Curator",
            "domain": "Obsidian vault curation, runbook gardening, wikilinks indexing, daily logs (Chunkito 256k)",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "amber",
        },
        "coder": {
            "name": "Coder",
            "emoji": "💻",
            "role": "Autonomous Software Engineer",
            "domain": "Full-stack feature engineering, test-driven development, deep debugging, refactoring",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "blue",
        },
        "ingest": {
            "name": "Ingest",
            "emoji": "📥",
            "role": "Knowledge Ingest & Article Synthesizer",
            "domain": "Web extraction, PDF document synthesis, cognitive graph enrichment, note capture",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "teal",
        },
        "heffalump": {
            "name": "Heffalump",
            "emoji": "🐘",
            "role": "Deep Context Workhorse & Latent Reasoning Titan",
            "domain": "Long-horizon codebase synthesis, 284B MoE deep reasoning, knotty multi-step implementations (DeepSeek-V4 284B @ 262k on chunkito)",
            "default_model": "deepseek-v4:262k",
            "accent_color": "purple",
        },
    }

    def attribute_session_persona(self, session: Dict[str, Any]) -> Optional[str]:
        """Infer which Pantheon swarm agent a session belongs to."""
        # 1. Direct profile name match
        prof = (session.get("profile_name") or "").lower()
        if prof in self.ROSTER_META:
            return prof

        # 2. Textual persona mentions in title, goal, context, cwd, custom notes, or first prompt
        text = f"{session.get('title') or ''} {session.get('goal') or ''} {session.get('context') or ''} {session.get('cwd') or ''} {session.get('first_prompt') or ''}".lower()
        if "@owl" in text or "owl:" in text or "🦉" in text or "pantheon-swarm" in text or "pantheon swarm" in text or "/pantheon-swarm" in text:
            return "owl"
        if "@rabbit" in text or "rabbit:" in text or "🐰" in text:
            return "rabbit"
        if "@jagular" in text or "jagular:" in text or "🐆" in text:
            return "jagular"
        if "@tigger" in text or "tigger:" in text or "🐯" in text:
            return "tigger"
        if "@piglet" in text or "piglet:" in text or "🐷" in text:
            return "piglet"
        if "@eeyore" in text or "eeyore:" in text or "🫏" in text:
            return "eeyore"
        if "@pooh" in text or "pooh:" in text or "🐻" in text:
            return "pooh"
        if "@coder" in text or "coder:" in text or "💻" in text:
            return "coder"
        if "@ingest" in text or "ingest:" in text or "📥" in text:
            return "ingest"
        if "@heffalump" in text or "heffalump:" in text or "🐘" in text:
            return "heffalump"

        # 3. Model & source matching for subagents and delegations
        is_subagent = (
            session.get("source") == "subagent"
            or bool(session.get("parent_session_id"))
            or "delegation_id" in session
            or session.get("is_subagent")
        )
        model = (session.get("model") or "").lower()
        provider = (session.get("billing_provider") or "").lower()

        if is_subagent:
            if "ernie" in model:
                return "eeyore"
            elif "qwen3.5" in model:
                return "piglet"
            elif "qwen3-30b" in model:
                return "rabbit"
            elif "qwen3-coder" in model:
                return "coder"
            elif "qwen3.8-flash-next" in model:
                if re.search(r'\b(vault|gardener|gardening|curat|curating|wikilink|runbook)\b', text, re.IGNORECASE):
                    return "pooh"
                return "jagular"
            elif "deepseek" in model:
                return "heffalump"
            elif "qwen3.8-27b" in model or "qwen3.8" in model or "chunkito" in provider or "chunkito" in model:
                if re.search(r'\b(vault|gardener|gardening|curat|curating|wikilink|runbook)\b', text, re.IGNORECASE):
                    return "pooh"
                return "tigger"
            elif "gemini" in model:
                return "owl"

        return None

    def get_pantheon_profiles(self) -> List[Dict[str, Any]]:
        """Scan ~/.hermes/profiles/ and aggregate metadata, sessions, tokens, skills, and memory for each Pantheon swarm agent."""
        profiles_dir = HERMES_DIR / "profiles"
        roster_meta = self.ROSTER_META

        agent_profiles = []
        if not profiles_dir.exists():
            return agent_profiles

        import yaml

        # Fetch all subagent sessions from main state.db for persona aggregation
        main_conn = self._get_ro_conn()
        all_main_sessions = []
        if main_conn:
            try:
                with main_conn:
                    m_cur = main_conn.cursor()
                    rows = m_cur.execute("""
                        SELECT id, title, model, started_at, ended_at, message_count, 
                               tool_call_count, input_tokens, output_tokens, cache_read_tokens, 
                               estimated_cost_usd, git_branch, profile_name, source, parent_session_id, cwd, billing_provider
                        FROM sessions
                    """).fetchall()
                    for row in rows:
                        s_dict = dict(row)
                        try:
                            msg_row = m_cur.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1", (s_dict["id"],)).fetchone()
                            if msg_row and msg_row[0]:
                                s_dict["first_prompt"] = msg_row[0]
                                if not s_dict.get("title") or str(s_dict.get("title")).startswith("[Workspace::v1:"):
                                    s_dict["title"] = msg_row[0][:80].replace("\n", " ").strip()
                            elif not s_dict.get("title") and (s_dict.get("source") == "subagent" or s_dict.get("parent_session_id")):
                                s_dict["title"] = f"Subagent Task ({s_dict['id']})"
                        except Exception:
                            pass
                        all_main_sessions.append(s_dict)
            except Exception:
                pass

        for p_id, meta in roster_meta.items():
            p_dir = profiles_dir / p_id

            # 1. Profile config & soul
            prof_file = p_dir / "profile.yaml" if p_dir.exists() else None
            prof_data = {}
            if prof_file and prof_file.exists():
                try:
                    prof_data = yaml.safe_load(prof_file.read_text(encoding="utf-8")) or {}
                except Exception:
                    pass

            conf_file = p_dir / "config.yaml" if p_dir.exists() else None
            conf_data = {}
            if conf_file and conf_file.exists():
                try:
                    conf_data = yaml.safe_load(conf_file.read_text(encoding="utf-8")) or {}
                except Exception:
                    pass

            soul_file = p_dir / "SOUL.md" if p_dir.exists() else None
            soul_content = soul_file.read_text(encoding="utf-8").strip() if soul_file and soul_file.exists() else ""

            # Extract first paragraph of SOUL as personality summary
            soul_summary = ""
            if soul_content:
                paragraphs = [p.strip() for p in soul_content.split("\n\n") if p.strip() and not p.strip().startswith("#")]
                soul_summary = paragraphs[0] if paragraphs else soul_content[:200]

            model_name = conf_data.get("model", {}).get("default") or meta.get("default_model", "qwen3.8-flash-next:262k")
            provider_name = conf_data.get("model", {}).get("provider") or ("chunkito" if self._is_local_model(model_name) else "gemini")
            is_local = self._is_local_model(model_name, provider_name)

            # 2. Collect sessions from standalone profile DB (if exists)
            p_db = p_dir / "state.db" if p_dir.exists() else None
            sess_count = 0
            tokens_in = 0
            tokens_out = 0
            cache_read = 0
            cost_usd = 0.0
            msg_count = 0
            collected_sessions = []
            seen_ids = set()

            if p_db and p_db.exists():
                try:
                    conn = sqlite3.connect(f"file:{p_db}?mode=ro", uri=True)
                    conn.row_factory = sqlite3.Row
                    with conn:
                        cur = conn.cursor()
                        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                        if "sessions" in tables:
                            rows = cur.execute("""
                                SELECT id, title, model, started_at, message_count, tool_call_count,
                                       input_tokens, output_tokens, cache_read_tokens, estimated_cost_usd 
                                FROM sessions ORDER BY started_at DESC
                            """).fetchall()
                            for r in rows:
                                rd = dict(r)
                                seen_ids.add(rd["id"])
                                collected_sessions.append(rd)
                                sess_count += 1
                                tokens_in += rd.get("input_tokens") or 0
                                tokens_out += rd.get("output_tokens") or 0
                                cache_read += rd.get("cache_read_tokens") or 0
                                cost_usd += rd.get("estimated_cost_usd") or 0.0
                                msg_count += rd.get("message_count") or 0
                except Exception:
                    pass

            # 3. Collect attributed sessions & subagents from main database
            for s in all_main_sessions:
                if s["id"] in seen_ids:
                    continue
                # Match either exact profile_name or attributed persona
                if s.get("profile_name") == p_id or self.attribute_session_persona(s) == p_id:
                    seen_ids.add(s["id"])
                    collected_sessions.append(s)
                    sess_count += 1
                    tokens_in += s.get("input_tokens") or 0
                    tokens_out += s.get("output_tokens") or 0
                    cache_read += s.get("cache_read_tokens") or 0
                    cost_usd += s.get("estimated_cost_usd") or 0.0
                    msg_count += s.get("message_count") or 0

            # Sort collected sessions by started_at desc
            collected_sessions.sort(key=lambda x: x.get("started_at") or 0, reverse=True)

            # 4. Count specialized skills
            p_skills_dir = p_dir / "skills" if p_dir.exists() else None
            skills_count = len(list(p_skills_dir.rglob("SKILL.md"))) if p_skills_dir and p_skills_dir.exists() else 0

            # 5. Memory & User profile sizes
            p_mem_file = p_dir / "MEMORY.md" if p_dir.exists() else None
            p_user_file = p_dir / "USER.md" if p_dir.exists() else None
            mem_chars = len(p_mem_file.read_text(encoding="utf-8")) if p_mem_file and p_mem_file.exists() else 0
            user_chars = len(p_user_file.read_text(encoding="utf-8")) if p_user_file and p_user_file.exists() else 0

            agent_profiles.append({
                "id": p_id,
                "name": meta["name"],
                "emoji": meta["emoji"],
                "role": meta["role"],
                "domain": meta["domain"],
                "accent_color": meta["accent_color"],
                "description": prof_data.get("description") or meta["domain"],
                "personality_summary": soul_summary,
                "model": model_name,
                "provider": provider_name,
                "is_local": is_local,
                "provider_category": self._classify_provider(model_name),
                "total_sessions": sess_count,
                "total_messages": msg_count,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "total_tokens": tokens_in + tokens_out,
                "cache_read_tokens": cache_read,
                "estimated_cost_usd": round(cost_usd, 4),
                "skills_count": skills_count,
                "memory_chars": mem_chars,
                "user_chars": user_chars,
                "has_soul": bool(soul_content),
                "recent_sessions": collected_sessions[:5],
                "_all_sessions": collected_sessions,
            })

        return agent_profiles

    def get_pantheon_profile_detail(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full details, SOUL.md content, memory, skills, and session history for a specific profile."""
        profiles = self.get_pantheon_profiles()
        summary = next((p for p in profiles if p["id"] == profile_id), None)
        if not summary:
            return None

        # Extract collected sessions from summary cache
        all_sessions = summary.pop("_all_sessions", [])

        p_dir = HERMES_DIR / "profiles" / profile_id
        import yaml

        soul_file = p_dir / "SOUL.md" if p_dir.exists() else None
        soul_raw = soul_file.read_text(encoding="utf-8") if soul_file and soul_file.exists() else ""

        mem_file = p_dir / "MEMORY.md" if p_dir.exists() else None
        mem_raw = mem_file.read_text(encoding="utf-8") if mem_file and mem_file.exists() else ""

        user_file = p_dir / "USER.md" if p_dir.exists() else None
        user_raw = user_file.read_text(encoding="utf-8") if user_file and user_file.exists() else ""

        conf_file = p_dir / "config.yaml" if p_dir.exists() else None
        conf_raw = conf_file.read_text(encoding="utf-8") if conf_file and conf_file.exists() else ""
        conf_data = yaml.safe_load(conf_raw) if conf_raw else {}

        # Profile skills
        skills_list = []
        p_skills_dir = p_dir / "skills" if p_dir.exists() else None
        if p_skills_dir and p_skills_dir.exists():
            for sk_path in p_skills_dir.rglob("SKILL.md"):
                try:
                    sk_content = sk_path.read_text(encoding="utf-8")
                    sk_meta = {}
                    if sk_content.startswith("---"):
                        parts = sk_content.split("---", 2)
                        if len(parts) >= 3:
                            sk_meta = yaml.safe_load(parts[1]) or {}
                    skills_list.append({
                        "name": sk_meta.get("name") or sk_path.parent.name,
                        "description": sk_meta.get("description", ""),
                        "category": sk_meta.get("category", "specialized"),
                        "path": str(sk_path),
                    })
                except Exception:
                    pass

        # Cron jobs
        cron_list = []
        p_cron_dir = p_dir / "cron" if p_dir.exists() else None
        if p_cron_dir and p_cron_dir.exists():
            for cf in p_cron_dir.glob("*.json"):
                try:
                    cdata = json.loads(cf.read_text(encoding="utf-8"))
                    cdata["file"] = cf.name
                    cron_list.append(cdata)
                except Exception:
                    pass

        return {
            **summary,
            "soul_raw": soul_raw,
            "memory_raw": mem_raw,
            "user_raw": user_raw,
            "config_raw": conf_raw,
            "config": conf_data,
            "skills": skills_list,
            "sessions": all_sessions,
            "cron_jobs": cron_list,
        }


hermes_reader = HermesReader()
