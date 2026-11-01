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


def saved_text(path):
    data = path.read_bytes()
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return data.decode(encoding).replace("\r\n", "\n")


async def finish_notepad(root, window_id, hint, text, approve):
    """Save and close the owned tab only; never dismiss a save/discard dialog."""
    import asyncio
    from sentinel_agent.computer_use.desktop_windows import WindowsDesktopSession
    from sentinel_agent.computer_use.desktop_schema import DesktopAction
    path = root / "artifacts" / hint
    folder = (root / "artifacts").resolve()
    if path.is_symlink() or folder.parent != root.resolve() or path.resolve().parent != folder:
        raise ValueError("Unsafe scratch file")
    session = WindowsDesktopSession(window_id, capture_images=False)
    await session.start()
    try:
        for key, kind in (("CTRL+S", "save_document"), ("CTRL+W", "close_document")):
            if not await approve({"window": hint, "output": str(path),
                                  "action": {"kind": kind, "filename": hint}}):
                return {"status": "needs_review", "reason": "Notepad left open: action declined"}
            await asyncio.sleep(.2)
            await session.focus()
            snapshot = await session.observe()
            if hint not in snapshot.title or snapshot.text.replace("\r\n", "\n") != text.replace("\r\n", "\n"):
                raise ValueError("Scratch document changed; leaving Notepad open")
            if key == "CTRL+W" and saved_text(path) != text.replace("\r\n", "\n"):
                raise ValueError("Saved text changed; leaving Notepad open")
            action = DesktopAction(kind="press_key", snapshot_id=snapshot.id, key=key)
            await session.validate(action)
            await session.act(action)
            if key == "CTRL+S":
                for _ in range(40):
                    if saved_text(path) == text.replace("\r\n", "\n"):
                        break
                    await asyncio.sleep(.1)
                else:
                    raise ValueError("Saved text did not verify; leaving Notepad open")
            else:
                for _ in range(40):
                    windows = WindowsDesktopSession.list_windows()
                    if not any(hint in w["title"] for w in windows):
                        break
                    await asyncio.sleep(.1)
                else:
                    raise ValueError("Notepad did not close; no dialog will be dismissed")
        if saved_text(path) != text.replace("\r\n", "\n"):
            raise ValueError("Final saved text verification failed")
        return {"status": "completed", "reason": "Notepad saved, verified and closed", "saved_file": str(path)}
    finally:
        await session.close()
