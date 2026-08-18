"""Hermes Karma configuration and directory paths."""
from pathlib import Path
import os

HERMES_DIR = Path(os.path.expanduser(os.getenv("HERMES_HOME", "~/.hermes")))
HERMES_STATE_DB = HERMES_DIR / "state.db"
HERMES_CONFIG_YAML = HERMES_DIR / "config.yaml"
HERMES_MEMORY_FILE = HERMES_DIR / "MEMORY.md"
HERMES_USER_FILE = HERMES_DIR / "USER.md"
HERMES_SKILLS_DIR = HERMES_DIR / "skills"
HERMES_CRON_DIR = HERMES_DIR / "cron"
HERMES_PLUGINS_DIR = HERMES_DIR / "plugins"

KARMA_DIR = Path(os.path.expanduser(os.getenv("HERMES_KARMA_HOME", "~/.hermes_karma")))
KARMA_METADATA_DB = KARMA_DIR / "metadata.db"
KARMA_LIVE_DIR = KARMA_DIR / "live_sessions"

# Ensure Karma directories exist
KARMA_DIR.mkdir(parents=True, exist_ok=True)
KARMA_LIVE_DIR.mkdir(parents=True, exist_ok=True)
