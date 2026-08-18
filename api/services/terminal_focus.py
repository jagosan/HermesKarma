"""Terminal Focus IPC service.
Brings active agent terminal windows, tmux panes, or desktop processes into foreground focus.
"""
import subprocess
import shutil
import platform
import os
from typing import Dict, Any, Optional


class TerminalFocusService:
    def focus_session(
        self,
        session_id: str,
        pid: Optional[int] = None,
        tmux_pane: Optional[str] = None,
        tty: Optional[str] = None,
    ) -> Dict[str, Any]:
        os_type = platform.system().lower()
        results = []

        # 1. Try tmux focus if pane specified
        if tmux_pane and shutil.which("tmux"):
            try:
                res = subprocess.run(
                    ["tmux", "select-pane", "-t", tmux_pane],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if res.returncode == 0:
                    results.append(f"tmux: focused pane {tmux_pane}")
                    return {"success": True, "method": "tmux", "details": results}
            except Exception as e:
                results.append(f"tmux error: {str(e)}")

        # 2. Linux X11 / Wayland window focus
        if "linux" in os_type:
            if pid:
                # Try xdotool
                if shutil.which("xdotool"):
                    try:
                        res = subprocess.run(
                            ["xdotool", "search", "--pid", str(pid), "windowactivate"],
                            capture_output=True,
                            text=True,
                            timeout=2
                        )
                        if res.returncode == 0:
                            results.append(f"xdotool: activated window for PID {pid}")
                            return {"success": True, "method": "xdotool", "details": results}
                    except Exception as e:
                        results.append(f"xdotool error: {str(e)}")

                # Try wmctrl
                if shutil.which("wmctrl"):
                    try:
                        res = subprocess.run(
                            ["wmctrl", "-a", f"PID {pid}"],
                            capture_output=True,
                            text=True,
                            timeout=2
                        )
                        if res.returncode == 0:
                            results.append(f"wmctrl: focused window for PID {pid}")
                            return {"success": True, "method": "wmctrl", "details": results}
                    except Exception as e:
                        results.append(f"wmctrl error: {str(e)}")

        # 3. macOS AppleScript focus
        elif "darwin" in os_type:
            if shutil.which("osascript"):
                try:
                    script = 'tell application "Terminal" to activate'
                    res = subprocess.run(
                        ["osascript", "-e", script],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    if res.returncode == 0:
                        results.append("osascript: activated Terminal application")
                        return {"success": True, "method": "osascript", "details": results}
                except Exception as e:
                    results.append(f"osascript error: {str(e)}")

        return {
            "success": False,
            "message": "Could not identify active desktop window or terminal pane for PID/pane.",
            "details": results,
            "session_id": session_id,
            "pid": pid,
            "tmux_pane": tmux_pane
        }


terminal_focus_service = TerminalFocusService()
