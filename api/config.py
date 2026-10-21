"""Hermes Karma configuration and directory paths."""
from pathlib import Path
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

HERMES_DIR = Path(os.path.expanduser(os.getenv("HERMES_HOME", "~/.hermes")))
HERMES_STATE_DB = HERMES_DIR / "state.db"
HERMES_CONFIG_YAML = HERMES_DIR / "config.yaml"
HERMES_MEMORY_FILE = HERMES_DIR / "MEMORY.md"
HERMES_USER_FILE = HERMES_DIR / "USER.md"
HERMES_SKILLS_DIR = HERMES_DIR / "skills"
HERMES_CRON_DIR = HERMES_DIR / "cron"
HERMES_PLUGINS_DIR = HERMES_DIR / "plugins"
HERMES_PROFILES_DIR = HERMES_DIR / "profiles"

KARMA_DIR = Path(os.path.expanduser(os.getenv("HERMES_KARMA_HOME", "~/.hermes_karma")))
KARMA_METADATA_DB = KARMA_DIR / "metadata.db"
KARMA_LIVE_DIR = KARMA_DIR / "live_sessions"

# Webhook secret configurations
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
LINEAR_WEBHOOK_SECRET = os.getenv("LINEAR_WEBHOOK_SECRET", "")
JIRA_WEBHOOK_SECRET = os.getenv("JIRA_WEBHOOK_SECRET", "")

# Fleet & Multi-Node Telemetry Configuration
DEFAULT_NODES = [
    {
        "id": os.getenv("COORDINATOR_NODE_ID", "beehive"),
        "name": os.getenv("COORDINATOR_NODE_NAME", "Beehive (Coordinator)"),
        "host": os.getenv("COORDINATOR_HOST", "localhost"),
        "tailscale_ip": os.getenv("BEEHIVE_TAILSCALE_IP", os.getenv("COORDINATOR_TAILSCALE_IP", "")),
        "role": "coordinator",
        "is_local": True,
        "ollama_port": int(os.getenv("LOCAL_OLLAMA_PORT", os.getenv("COORDINATOR_OLLAMA_PORT", 11434))),
        "hardware": {
            "cpu": "AMD Ryzen 7 8845HS (16 threads)",
            "ram_gb": 32,
            "gpu": "Radeon 780M iGPU",
            "vram_gb": 16,
            "max_loaded_models": 1,
        },
        "tags": ["gateway", "coordinator", "sqlite", "fastapi", "beehive"],
    },
    {
        "id": os.getenv("REMOTE_WORKER_NODE_ID", "chunkito"),
        "name": os.getenv("REMOTE_WORKER_NODE_NAME", "Chunkito (AMD APU Inference)"),
        "host": os.getenv("CHUNKITO_HOST", os.getenv("REMOTE_WORKER_HOST", "127.0.0.1")),
        "tailscale_ip": os.getenv("CHUNKITO_TAILSCALE_IP", os.getenv("REMOTE_WORKER_TAILSCALE_IP", "")),
        "role": "inference_worker",
        "is_local": False,
        "ollama_port": int(os.getenv("CHUNKITO_OLLAMA_PORT", os.getenv("REMOTE_WORKER_OLLAMA_PORT", 11434))),
        "hardware": {
            "cpu": "AMD Ryzen AI Max+ 395 (Strix Halo APU, 16c/32t)",
            "ram_gb": 128,
            "vram_gb": 118,
            "gpu": "AMD Radeon 8060S APU (118GB unified via amdgpu.gttsize=120832)",
            "gtt_size_mb": 120832,
            "max_loaded_models": 1,
        },
        "tags": ["amd-apu", "strix-halo", "ollama", "rocm", "tailscale", "moa-worker"],
    },
]

# Ensure Karma directories exist
KARMA_DIR.mkdir(parents=True, exist_ok=True)
KARMA_LIVE_DIR.mkdir(parents=True, exist_ok=True)
