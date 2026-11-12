"""Service definitions: load/save services.json (next to the .exe or script, so it is portable)."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


CONFIG_PATH = app_dir() / "services.json"


@dataclass
class Service:
    name: str
    start_cmd: str = ""
    stop_cmd: str = ""
    shell: str = "cmd"            # "cmd" or "powershell"
    port: int = 0                 # 0 = no port to check
    process_match: str = ""       # process name or command-line fragment (fallback status)
    cwd: str = ""
    show_console: bool = True     # start in a visible console window
    kill_leftovers: bool = True   # after Stop, kill whatever still holds the port / matches the process

    @staticmethod
    def from_dict(d: dict) -> "Service":
        known = {k: v for k, v in d.items() if k in Service.__dataclass_fields__}
        return Service(**known)


DEFAULTS = [
    Service("SearXNG", 'call "E:\\StartTools\\startsearxng.cmd"', 'call "E:\\StartTools\\stop_searxng.cmd"',
            port=8888),
    Service("Colibri", 'call "E:\\StartTools\\Start_Colibri.cmd"', 'call "E:\\StartTools\\Stop_Colibri.cmd"',
            port=8000),
    Service("ComfyUI", 'call "E:\\StartTools\\Start_ComfyUI.bat"', "", port=8188, process_match="comfy"),
    Service("Pixelle", 'call "E:\\StartTools\\Start_Pixelle.bat"', "", port=8501, process_match="streamlit"),
    Service("Bionic", 'call "E:\\StartTools\\Start_Bionic.bat"', "", port=0, process_match="Bionic.exe"),
    Service("Shorts Generator", 'call "E:\\StartTools\\Start_AI_Youtube_Shorts_Generator.bat"', "",
            port=0, process_match="start_GUI.py"),
]


def load() -> list[Service]:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return [Service.from_dict(d) for d in data.get("services", [])]
        except (OSError, ValueError, TypeError):
            pass
    if Path("E:/StartTools").is_dir():   # the author's own setup; everyone else starts empty
        return [Service(**asdict(s)) for s in DEFAULTS]
    return []


def save(services: list[Service]) -> None:
    payload = {"services": [asdict(s) for s in services]}
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
