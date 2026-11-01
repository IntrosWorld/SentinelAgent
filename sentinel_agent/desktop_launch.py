"""Explicit launch intents; never interpret user input as a shell command."""
import re
from pathlib import Path

from sentinel_agent.desktop import DesktopEnvironment


def launch_intent(goal):
    match = re.fullmatch(
        r"\s*(?:open|launch|start)\s+(notepad|calculator)"
        r"(?:\s+and\s+(?:write|type)\s+([\s\S]+))?\s*", goal, re.I
    )
    if not match:
        return None
    app, text = match.groups()
    app = app.lower()
    if text and app != "notepad":
        raise ValueError("Writing after launch is currently supported for Notepad only")
    if text and len(text) > 300:
        raise ValueError("Notepad launch text is limited to 300 characters for complete readback verification")
    return app, text


def launch_app(root: Path, app: str):
    # Reuse the existing fixed executable allowlist, not a user-supplied path.
    import subprocess
    from uuid import uuid4
    executable = DesktopEnvironment.app_commands[app]
    if not executable.is_file():
        raise ValueError("Allowlisted application is not installed")
    command = [str(executable)]
    hint = None
    if app == "notepad":
        folder = root / "artifacts"
        folder.mkdir(exist_ok=True)
        if folder.is_symlink() or folder.resolve().parent != root.resolve():
            raise ValueError("Unsafe scratch folder")
        scratch = folder / f"sentinel_scratch_{uuid4().hex}.txt"
        scratch.touch(exist_ok=False)
        command.append(str(scratch))
        hint = scratch.name
    subprocess.Popen(command, shell=False)
    return hint
