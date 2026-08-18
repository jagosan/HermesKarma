"""Install script for Hermes Karma Lifecycle Hook."""
import os
import shutil
from pathlib import Path

def install_plugin():
    hermes_home = Path(os.path.expanduser(os.getenv("HERMES_HOME", "~/.hermes")))
    plugins_dir = hermes_home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    src_hook = Path(__file__).resolve().parent.parent / "plugins" / "hermes_karma_hook.py"
    dst_hook = plugins_dir / "hermes_karma_hook.py"

    if dst_hook.exists() or dst_hook.is_symlink():
        try:
            dst_hook.unlink()
        except Exception:
            pass

    try:
        # Prefer symlink for live development
        dst_hook.symlink_to(src_hook)
        print(f"✓ Successfully symlinked {src_hook} -> {dst_hook}")
    except Exception:
        # Fallback to copy
        shutil.copy(src_hook, dst_hook)
        print(f"✓ Successfully copied {src_hook} -> {dst_hook}")

if __name__ == "__main__":
    install_plugin()
