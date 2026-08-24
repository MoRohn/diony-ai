"""First open: install the desk and start Studio + Producer.

Stdlib only until the venv exists. Double-click Open Diony AI — no pip in the UI.
"""

from __future__ import annotations

import http.client
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

WIZARD_PORT = 8787
PRODUCER_PORT = 8788
DESK_PORT = 8765
STUDIO_URL = "http://studio.diony.local"
PRODUCER_URL = "http://producer.diony.local"
MIN_PY = (3, 11)

EDITABLES = (
    "packages/contracts",
    "apps/wizard",
    "apps/wan",
    "apps/finish",
    "apps/producer",
    "apps/conductor",
    "apps/desk",
)

STATIC = Path(__file__).resolve().parent / "static"

_logs: list = []

_state: dict = {
    "phase": "looking",
    "title": "Opening the desk",
    "detail": "Looking for Python 3.11.",
    "studio": STUDIO_URL,
    "producer": PRODUCER_URL,
    "python": "",
    "error": "",
    "diagnostics": "",
    "runtime_mode": (
        "installed" if bool(getattr(sys, "frozen", False)) else "source-checkout"
    ),
    "log": [],
}
_lock = threading.Lock()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_product_root() -> Path | None:
    explicit = os.environ.get("DIONY_APP_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    bundle = getattr(sys, "_MEIPASS", "")
    if is_frozen() and bundle:
        return (Path(str(bundle)) / "product").resolve()
    return None


def user_data_dir(
    *,
    platform_name: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Normal per-user writable location; installed application files stay immutable."""
    platform_name = platform_name or sys.platform
    environ = dict(os.environ if environ is None else environ)
    home = home or Path.home()
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / "Diony AI"
    if platform_name.startswith("win"):
        local = environ.get("LOCALAPPDATA", "").strip()
        return (Path(local) if local else home / "AppData" / "Local") / "Diony AI"
    xdg = environ.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg) if xdg else home / ".local" / "share") / "diony-ai"


def repo_root() -> Path:
    bundled = bundled_product_root()
    if bundled is not None:
        profile = bundled / "infra" / "profiles" / "spark-4x-v1.yaml"
        if profile.is_file():
            return bundled
        raise FileNotFoundError(
            f"Diony AI application resources are incomplete: {profile}. Reinstall the application."
        )
    here = Path(__file__).resolve()
    for path in [here, *here.parents]:
        if (path / "apps" / "wizard").is_dir() and (path / "packages" / "contracts").is_dir():
            return path
    raise FileNotFoundError("Diony AI repo not found above this file")


def venv_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / ".venv"


def venv_python(root: Path | None = None) -> Path:
    root = root or repo_root()
    if os.name == "nt":
        return venv_dir(root) / "Scripts" / "python.exe"
    return venv_dir(root) / "bin" / "python"


def venv_script(name: str, root: Path | None = None) -> Path:
    root = root or repo_root()
    if os.name == "nt":
        return venv_dir(root) / "Scripts" / f"{name}.exe"
    return venv_dir(root) / "bin" / name


def marker_path(root: Path | None = None) -> Path:
    return venv_dir(root) / ".diony-desk-ok"


def data_dir(root: Path | None = None) -> Path:
    explicit = os.environ.get("DIONY_DESK_DATA", "").strip()
    installed = is_frozen() or os.environ.get("DIONY_INSTALLED_APP", "").strip() == "1"
    if explicit:
        dest = Path(explicit).expanduser()
    elif installed:
        dest = user_data_dir()
    else:
        dest = (root or repo_root()) / "apps" / "desk" / "data"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def configure_runtime_environment(root: Path | None = None) -> Path:
    """Give bundled services immutable resources and user-owned mutable state."""
    root = root or repo_root()
    if is_frozen() or os.environ.get("DIONY_INSTALLED_APP", "").strip() == "1":
        writable = user_data_dir()
        writable.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("DIONY_APP_ROOT", str(root))
        os.environ.setdefault("DIONY_APP_DATA", str(writable))
        os.environ.setdefault("DIONY_DESK_DATA", str(writable))
        os.environ.setdefault("DIONY_VAULT", str(writable / "vault"))
        os.environ.setdefault("DIONY_PRODUCER_VAULT", str(writable / "vault"))
    return root


def desktop_release_info(root: Path | None = None) -> dict:
    root = root or repo_root()
    try:
        value = json.loads((root / "desktop-release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def desk_ui_stamp(root: Path | None = None) -> str:
    """Cache token from the Desk HTML (`app.js?v=`)."""
    html = (
        (root / "apps" / "desk" / "src" / "diony_desk" / "static" / "index.html")
        if root is not None
        else STATIC / "index.html"
    )
    try:
        text = html.read_text(encoding="utf-8")
    except OSError:
        return ""
    token = "app.js?v="
    start = text.find(token)
    if start < 0:
        return ""
    rest = text[start + len(token) :]
    return rest.split('"', 1)[0].split("'", 1)[0].split("&", 1)[0].split(" ", 1)[0].strip()


def application_identity(root: Path | None = None) -> str:
    """Identity of every file embedded in this app, or the source Desk UI."""
    try:
        release = desktop_release_info(root)
    except (OSError, FileNotFoundError):
        release = {}
    payload = str(release.get("payload_sha256") or "").strip()
    if payload:
        return f"payload:{payload}"
    return f"source:{desk_ui_stamp(root)}"


def diagnostic_snapshot(root: Path | None = None) -> dict:
    root = root or repo_root()
    release = desktop_release_info(root)
    state_root = data_dir(root)
    logs = state_root / "logs" if is_frozen() else state_root
    log_files = []
    if logs.is_dir():
        for path in sorted(logs.glob("*.log")):
            try:
                log_files.append({"path": str(path), "bytes": path.stat().st_size})
            except OSError:
                continue
    return {
        "schema_version": 1,
        "product": "Diony AI",
        "version": str(release.get("version") or "source-checkout"),
        "payload_sha256": str(release.get("payload_sha256") or ""),
        "runtime_mode": "installed" if is_frozen() else "source-checkout",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "executable": str(Path(sys.executable)),
        "application_root": str(root),
        "data_dir": str(state_root),
        "logs": log_files,
        "services": {
            "wizard_port_open": port_open(WIZARD_PORT),
            "wizard_current": wizard_is_current(root),
            "producer_port_open": port_open(PRODUCER_PORT),
            "producer_current": producer_is_current(root),
            "product_names_live": product_names_live(),
        },
    }


def write_diagnostic_report(root: Path | None = None) -> Path:
    root = root or repo_root()
    destination = data_dir(root) / "diagnostics.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(diagnostic_snapshot(root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def public_state() -> dict:
    with _lock:
        return {
            "product": "Diony AI",
            "application_identity": application_identity(),
            "phase": _state["phase"],
            "title": _state["title"],
            "detail": _state["detail"],
            "studio": _state["studio"],
            "producer": _state["producer"],
            "python": _state["python"],
            "error": _state["error"],
            "diagnostics": _state["diagnostics"],
            "runtime_mode": _state["runtime_mode"],
            "log": list(_state["log"][-12:]),
        }


def set_state(**kwargs: object) -> None:
    with _lock:
        _state.update(kwargs)
        if kwargs.get("detail"):
            _state["log"].append(str(kwargs["detail"])[:240])


def say(detail: str, **kwargs: object) -> None:
    set_state(detail=detail, **kwargs)
    print(detail, flush=True)


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def desk_status(host: str = "127.0.0.1", port: int = DESK_PORT) -> dict:
    """Read the local Desk status without treating arbitrary listeners as Diony."""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=1.2)
        conn.request("GET", "/api/status")
        response = conn.getresponse()
        raw = response.read(12000)
        status = response.status
        conn.close()
    except OSError:
        return {}
    if status != 200:
        return {}
    try:
        payload = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("product") != "Diony AI":
        return {}
    return payload


def desk_state_ok(host: str = "127.0.0.1", port: int = DESK_PORT) -> bool:
    """Distinguish an existing Diony Desk from an unrelated listener."""
    return bool(desk_status(host, port))


def desk_is_current(root: Path | None = None) -> bool:
    """Only hand off to a Desk serving the exact current packaged payload."""
    status = desk_status()
    return bool(status) and status.get("application_identity") == application_identity(root)


def wait_port(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(0.25)
    return False


def python_version(binary: str) -> tuple[int, int] | None:
    try:
        out = subprocess.check_output(
            [binary, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            text=True,
            timeout=8,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.strip().split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def python_candidates() -> list[str]:
    names = [
        "python3.14",
        "python3.13",
        "python3.12",
        "python3.11",
        "python3",
        "python",
    ]
    extras = [
        "/opt/homebrew/bin/python3.14",
        "/opt/homebrew/bin/python3.13",
        "/opt/homebrew/bin/python3.12",
        "/opt/homebrew/bin/python3.11",
        "/usr/local/bin/python3.14",
        "/usr/local/bin/python3.13",
        "/usr/local/bin/python3.12",
        "/usr/local/bin/python3.11",
        "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
        sys.executable,
    ]
    found: list[str] = []
    for name in names:
        path = shutil.which(name)
        if path:
            found.append(path)
    found.extend(p for p in extras if Path(p).is_file())
    ordered: list[str] = []
    seen: set[str] = set()
    for item in found:
        key = str(Path(item).resolve()) if Path(item).exists() else item
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def find_python() -> tuple[str, tuple[int, int]] | None:
    for binary in python_candidates():
        ver = python_version(binary)
        if ver and ver >= MIN_PY:
            return binary, ver
    return None


def python_help_url() -> str:
    if sys.platform == "darwin":
        return "https://www.python.org/downloads/macos/"
    if os.name == "nt":
        return "https://www.python.org/downloads/windows/"
    return "https://www.python.org/downloads/"


def desk_already_installed(root: Path | None = None) -> bool:
    root = root or repo_root()
    if is_frozen():
        return (root / "desktop-release.json").is_file()
    return venv_script("diony-wizard", root).is_file() and marker_path(root).is_file()


def service_command(
    name: str,
    root: Path | None = None,
    *,
    frozen: bool | None = None,
    executable: Path | None = None,
) -> list[str]:
    if name not in {"wizard", "producer", "gateway"}:
        raise ValueError(f"unknown Diony service: {name}")
    bundled = is_frozen() if frozen is None else frozen
    if bundled:
        return [str(executable or Path(sys.executable)), "--service", name]
    root = root or repo_root()
    if name == "gateway":
        return [str(venv_python(root)), "-m", "diony_wizard.gateway"]
    return [str(venv_script(f"diony-{name}", root))]


def run(cmd: list[str], *, cwd: Path) -> None:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-1200:]
        raise RuntimeError(tail or f"command failed: {cmd[0]}")


def ensure_venv(py: str, root: Path) -> Path:
    dest = venv_dir(root)
    python = venv_python(root)
    if python.is_file():
        return python
    say("Making a private Python for this desk.")
    run([py, "-m", "venv", str(dest)], cwd=root)
    if not python.is_file():
        raise RuntimeError("Could not create the Diony Python environment.")
    return python


def install_packages(python: Path, root: Path) -> None:
    say(
        "This is once. A few minutes. Leave this window open.",
        phase="installing",
        title="Laying the desk",
    )
    run([str(python), "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"], cwd=root)
    args = [str(python), "-m", "pip", "install"]
    for rel in EDITABLES:
        args.extend(["-e", str(root / rel)])
    say("Installing Studio, Producer, and the rest of the desk.")
    run(args, cwd=root)
    marker_path(root).write_text("ok\n")


def wizard_state_ok(host: str = "127.0.0.1", port: int | None = None) -> bool:
    """True when GET /api/state on the Studio bind is 200 JSON with active_step."""
    dest = WIZARD_PORT if port is None else port
    try:
        conn = http.client.HTTPConnection(host, dest, timeout=1.5)
        conn.request("GET", "/api/state")
        resp = conn.getresponse()
        code = resp.status
        raw = resp.read(12000)
        conn.close()
    except OSError:
        return False
    if code != 200:
        return False
    try:
        payload = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and "active_step" in payload


def wizard_ui_stamp(root: Path | None = None) -> str:
    """Cache token from the on-disk Studio HTML (`app.js?v=`)."""
    html = (root or repo_root()) / "apps" / "wizard" / "src" / "diony_wizard" / "static" / "index.html"
    try:
        text = html.read_text(encoding="utf-8")
    except OSError:
        return ""
    token = "app.js?v="
    start = text.find(token)
    if start < 0:
        return ""
    rest = text[start + len(token) :]
    stamp = rest.split('"', 1)[0].split("'", 1)[0].split("&", 1)[0].split(" ", 1)[0].strip()
    return stamp


def wizard_html_ok(root: Path | None = None, host: str = "127.0.0.1", port: int | None = None) -> bool:
    """True when GET / on the Studio bind matches the on-disk UI stamp."""
    stamp = wizard_ui_stamp(root)
    if not stamp:
        return True
    dest = WIZARD_PORT if port is None else port
    try:
        conn = http.client.HTTPConnection(host, dest, timeout=1.5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        code = resp.status
        raw = resp.read()
        conn.close()
    except OSError:
        return False
    if code != 200:
        return False
    return stamp.encode("utf-8") in raw


def wizard_is_current(root: Path | None = None) -> bool:
    return wizard_state_ok() and wizard_html_ok(root)


def producer_state_ok(host: str = "127.0.0.1", port: int | None = None) -> bool:
    dest = PRODUCER_PORT if port is None else port
    try:
        conn = http.client.HTTPConnection(host, dest, timeout=1.5)
        conn.request("GET", "/api/project")
        resp = conn.getresponse()
        code = resp.status
        raw = resp.read()
        conn.close()
    except OSError:
        return False
    if code != 200:
        return False
    try:
        payload = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and bool(payload.get("id"))


def producer_ui_stamp(root: Path | None = None) -> str:
    html = (
        (root or repo_root())
        / "apps"
        / "producer"
        / "src"
        / "diony_producer"
        / "static"
        / "index.html"
    )
    try:
        text = html.read_text(encoding="utf-8")
    except OSError:
        return ""
    token = "app.js?v="
    start = text.find(token)
    if start < 0:
        return ""
    rest = text[start + len(token) :]
    return rest.split('"', 1)[0].split("'", 1)[0].split("&", 1)[0].split(" ", 1)[0].strip()


def producer_html_ok(
    root: Path | None = None, host: str = "127.0.0.1", port: int | None = None
) -> bool:
    stamp = producer_ui_stamp(root)
    if not stamp:
        return True
    dest = PRODUCER_PORT if port is None else port
    try:
        conn = http.client.HTTPConnection(host, dest, timeout=1.5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        code = resp.status
        raw = resp.read()
        conn.close()
    except OSError:
        return False
    return code == 200 and stamp.encode("utf-8") in raw


def producer_is_current(root: Path | None = None) -> bool:
    return producer_state_ok() and producer_html_ok(root)


def listener_pids(port: int) -> list[int]:
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["netstat", "-ano", "-p", "TCP"],
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        found: list[int] = []
        needle = f":{port}"
        for line in out.splitlines():
            if "LISTEN" not in line.upper():
                continue
            if needle not in line:
                continue
            parts = line.split()
            try:
                found.append(int(parts[-1]))
            except ValueError:
                continue
        return list(dict.fromkeys(found))
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.split():
        try:
            found.append(int(line))
        except ValueError:
            continue
    return list(dict.fromkeys(found))


def process_command(pid: int) -> str:
    try:
        if os.name == "nt":
            command = (
                "$p=Get-CimInstance Win32_Process -Filter \"ProcessId = "
                + str(int(pid))
                + "\"; if ($p) {$p.CommandLine}"
            )
            return subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", command],
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            ).strip()
        return subprocess.check_output(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def listener_is_diony(port: int) -> bool:
    signatures = (
        "diony-wizard",
        "diony-producer",
        "diony_wizard",
        "diony_producer",
        "--service wizard",
        "--service producer",
    )
    commands = [process_command(pid).lower() for pid in listener_pids(port)]
    return bool(commands) and all(any(mark in command for mark in signatures) for command in commands)


def listener_is_desk(port: int = DESK_PORT) -> bool:
    """True only for a source or packaged Diony Desk parent process."""
    signatures = (
        "diony_desk/boot.py",
        "diony_desk.boot",
        "diony-desk",
        "diony ai.app/contents/macos/diony ai",
        "diony.app/contents/macos/diony ai",
    )
    commands = [process_command(pid).lower() for pid in listener_pids(port)]
    return bool(commands) and all(any(mark in command for mark in signatures) for command in commands)


def stop_listener(port: int) -> None:
    """Stop a listener only after its caller has verified Diony ownership."""
    pids = listener_pids(port)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    deadline = time.time() + 5
    while time.time() < deadline and port_open(port):
        time.sleep(0.15)
    if not port_open(port):
        return
    sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    for pid in listener_pids(port):
        try:
            os.kill(pid, sig)
        except OSError:
            continue
    deadline = time.time() + 3
    while time.time() < deadline and port_open(port):
        time.sleep(0.15)


def claim_desk_port(root: Path | None = None) -> bool:
    """Return true for a current Desk; replace an older Diony Desk safely."""
    if not desk_state_ok():
        return False
    root = root or repo_root()
    if desk_is_current(root):
        return True
    if not listener_is_desk(DESK_PORT):
        raise RuntimeError(
            "Port 8765 reports Diony AI but is not owned by the Diony Desk. "
            "Close that application, then open Diony AI again."
        )
    say("A previous Diony AI build is open. Replacing it with this build.")
    stop_listener(DESK_PORT)
    if port_open(DESK_PORT):
        raise RuntimeError("The previous Diony Desk did not close. Open Diony AI again.")
    for service_port in (WIZARD_PORT, PRODUCER_PORT):
        if port_open(service_port) and listener_is_diony(service_port):
            stop_listener(service_port)
    return False


def start_app(command: Path | list[str], log_name: str, root: Path) -> None:
    log_root = data_dir(root) / "logs" if is_frozen() else data_dir(root)
    log_root.mkdir(parents=True, exist_ok=True)
    log = log_root / log_name
    env = os.environ.copy()
    env.setdefault("DIONY_DRY_RUN", "1")
    env.pop("DASH_API_KEY", None)
    env.pop("DASHSCOPE_API_KEY", None)
    handle = log.open("ab")
    _logs.append(handle)
    kwargs: dict = {
        "cwd": str(root),
        "env": env,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    argv = [str(command)] if isinstance(command, Path) else [str(item) for item in command]
    subprocess.Popen(argv, **kwargs)


def product_names_live() -> bool:
    """True when http://studio.diony.local reaches the Host router (not just :8787)."""
    host = urlparse(STUDIO_URL).hostname or "studio.diony.local"
    try:
        conn = http.client.HTTPConnection(host, 80, timeout=1.2)
        conn.request("GET", "/api/state")
        resp = conn.getresponse()
        names = (resp.getheader("X-Diony-Names") or "").lower()
        resp.read()
        conn.close()
    except OSError:
        return False
    return names.startswith(("studio", "wizard"))


PREPARING_STUDIO_COPY = (
    "Preparing the Diony AI Studio & Producer. This computer may ask for a password once."
)


def ensure_product_names(root: Path | None = None) -> bool:
    """Hosts file + :80 Host router so the product names work on this laptop.

    Runs `python -m diony_wizard.gateway ensure` from the desk venv. May show a
    one-time administrator password prompt. Does not import Studio (boot stays stdlib).
    """
    root = root or repo_root()
    python = venv_python(root)
    if not is_frozen() and not python.is_file():
        say("Product names wait until the desk Python exists.")
        return False
    if product_names_live():
        say("Studio is http://studio.diony.local. Producer is http://producer.diony.local.")
        return True
    say(PREPARING_STUDIO_COPY)
    env = os.environ.copy()
    env.setdefault("DIONY_DRY_RUN", "1")
    env.pop("DASH_API_KEY", None)
    env.pop("DASHSCOPE_API_KEY", None)
    try:
        proc = subprocess.run(
            [*service_command("gateway", root), "ensure"],
            cwd=str(root),
            env=env,
            timeout=200,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"Could not lay product names: {exc}")
        return False
    if proc.returncode != 0:
        say(
            "Approve the one-time password so http://studio.diony.local works, "
            "or run: python -m diony_wizard.gateway ensure"
        )
        return False
    say("Studio is http://studio.diony.local. Producer is http://producer.diony.local.")
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    deadline = time.time() + 8
    while time.time() < deadline:
        if product_names_live():
            return True
        time.sleep(0.25)
    return product_names_live()


def finish_open(*, names_ok: bool) -> None:
    if names_ok or product_names_live():
        say(
            "Studio is ready.",
            phase="ready",
            title="Opening Studio",
            error="",
        )
        return
    say(
        "Studio needs permission to use its local address.",
        phase="need_names",
        title="Opening Studio",
    )


_retrying_names = threading.Lock()


def retry_product_names() -> None:
    if not _retrying_names.acquire(blocking=False):
        return
    try:
        say(PREPARING_STUDIO_COPY, phase="starting", title="Opening Studio")
        ok = ensure_product_names()
        finish_open(names_ok=ok)
    finally:
        _retrying_names.release()


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def ensure_running(root: Path) -> None:
    wizard = service_command("wizard", root)
    producer = service_command("producer", root)
    if not is_frozen() and (
        not Path(wizard[0]).is_file() or not Path(producer[0]).is_file()
    ):
        raise RuntimeError("Install did not leave diony-wizard and diony-producer.")
    say("Starting Studio and Producer.", phase="starting", title="Opening Studio")
    if port_open(WIZARD_PORT) and not wizard_is_current(root):
        if not listener_is_diony(WIZARD_PORT):
            raise RuntimeError(
                "Port 8787 is already used by another application. Close that application, then open Diony AI again."
            )
        say("Studio is listening but stale or /api/state is not 200. Restarting Studio.")
        stop_listener(WIZARD_PORT)
    if port_open(PRODUCER_PORT) and not producer_is_current(root):
        if not listener_is_diony(PRODUCER_PORT):
            raise RuntimeError(
                "Port 8788 is already used by another application. Close that application, then open Diony AI again."
            )
        say("Producer is listening but stale or /api/project is not 200. Restarting Producer.")
        stop_listener(PRODUCER_PORT)
    if not port_open(WIZARD_PORT):
        start_app(wizard, "wizard.log", root)
    if not port_open(PRODUCER_PORT):
        start_app(producer, "producer.log", root)
    if not wait_port(WIZARD_PORT):
        raise RuntimeError("Studio did not open on port 8787.")
    if not wizard_is_current(root):
        say("Studio opened but is stale or /api/state failed. Restarting Studio once.")
        stop_listener(WIZARD_PORT)
        start_app(wizard, "wizard.log", root)
        if not wait_port(WIZARD_PORT) or not wizard_is_current(root):
            raise RuntimeError("Studio is not serving this desk’s UI after restart.")
    if not wait_port(PRODUCER_PORT, timeout=20):
        raise RuntimeError("Producer did not open on port 8788.")
    if not producer_is_current(root):
        say("Producer opened but is stale or /api/project failed. Restarting Producer once.")
        if not listener_is_diony(PRODUCER_PORT):
            raise RuntimeError(
                "Port 8788 changed ownership during startup. Diony AI left it untouched."
            )
        stop_listener(PRODUCER_PORT)
        start_app(producer, "producer.log", root)
        if not wait_port(PRODUCER_PORT, timeout=20) or not producer_is_current(root):
            raise RuntimeError("Producer is not serving this desk’s UI after restart.")
    finish_open(names_ok=ensure_product_names(root))


def work() -> None:
    try:
        if os.environ.get("DIONY_DESK_SKIP_INSTALL", "").strip() in {"1", "true", "yes"}:
            say("Install skipped for tests.", phase="ready", title="Dry desk")
            return
        root = configure_runtime_environment(repo_root())
        if is_frozen():
            ensure_running(root)
            return
        found = find_python()
        if not found:
            help_url = python_help_url()
            say(
                "Install Python 3.11 or newer, then open Diony AI again.",
                phase="need_python",
                title="Python 3.11 is the key",
                error=help_url,
            )
            print(help_url, flush=True)
            return
        py, ver = found
        set_state(python=f"{py} ({ver[0]}.{ver[1]})")
        if desk_already_installed(root) and port_open(WIZARD_PORT) and port_open(PRODUCER_PORT):
            if wizard_is_current(root) and producer_is_current(root):
                finish_open(names_ok=ensure_product_names(root))
                return
            say("A studio service is stale or not answering. Repairing the desk.")
            ensure_running(root)
            return
        python = ensure_venv(py, root)
        if not desk_already_installed(root):
            install_packages(python, root)
        ensure_running(root)
    except Exception as exc:  # noqa: BLE001 — surface to the lobby
        diagnostics = ""
        try:
            diagnostics = str(write_diagnostic_report(locals().get("root")))
        except Exception:
            pass
        set_state(
            phase="error",
            title="Could not open the desk",
            detail=(
                f"{str(exc)[:320]}"
                + (f" Diagnostics: {diagnostics}" if diagnostics else "")
            )[:500],
            error=str(exc)[:1200],
            diagnostics=diagnostics,
        )


class DeskHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=directory or str(STATIC), **kwargs)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
            return
        if path == "/api/status":
            body = json.dumps(public_state()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/ensure-names":
            threading.Thread(
                target=retry_product_names, name="diony-names-retry", daemon=True
            ).start()
            body = json.dumps(public_state()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/diagnostics":
            try:
                body = (
                    json.dumps(diagnostic_snapshot(), indent=2, sort_keys=True) + "\n"
                ).encode()
            except Exception as exc:
                body = json.dumps({"product": "Diony AI", "error": str(exc)[:500]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header(
                "Content-Disposition", 'attachment; filename="diony-ai-diagnostics.json"'
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(port: int = DESK_PORT) -> ThreadingHTTPServer:
    handler = partial(DeskHandler, directory=str(STATIC))
    ThreadingHTTPServer.allow_reuse_address = True
    last_error: OSError | None = None
    for candidate in range(port, port + 5):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError as exc:
            last_error = exc
    else:
        raise last_error or OSError(f"Could not bind desk opener near {port}")
    thread = threading.Thread(target=server.serve_forever, name="diony-desk", daemon=True)
    thread.start()
    return server


def lobby_url(port: int = DESK_PORT) -> str:
    return f"http://127.0.0.1:{port}/"


def should_open_lobby(*, already_installed: bool, frozen: bool) -> bool:
    """Keep normal app launches on the named Studio; lobby is source setup only."""
    return not frozen and not already_installed


def should_open_studio_after_work(*, lobby_opened: bool, phase: str) -> bool:
    """Open Studio only when no existing browser page can replace itself."""
    return phase == "ready" and not lobby_opened


def run_embedded_service(name: str, args: list[str]) -> int:
    """Dispatch child services through the same signed desktop executable."""
    configure_runtime_environment()
    if name == "wizard":
        from diony_wizard.app import main as wizard_main

        wizard_main()
        return 0
    if name == "producer":
        from diony_producer.app import main as producer_main

        producer_main()
        return 0
    if name == "gateway":
        from diony_wizard.gateway import main as gateway_main

        return int(gateway_main(args))
    print(f"Unknown Diony service: {name}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == "--service":
        return run_embedded_service(argv[1], argv[2:])
    try:
        configure_runtime_environment()
    except Exception as exc:
        print(f"Diony AI application resources are unavailable: {exc}", file=sys.stderr)
        return 1
    if argv and argv[0] in {"-h", "--help"}:
        print("Double-click Open Diony AI in the Diony AI folder.")
        print("Studio: http://studio.diony.local")
        print("Producer: http://producer.diony.local")
        print("Leave the window open until it says you can close it.")
        return 0
    try:
        current_desk_open = claim_desk_port()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    if current_desk_open:
        try:
            if desk_already_installed():
                ensure_product_names()
        except Exception:
            pass
        open_browser(STUDIO_URL)
        print(f"Diony AI is already open — {STUDIO_URL}", flush=True)
        print(f"Producer is {PRODUCER_URL}", flush=True)
        return 0
    set_state(
        phase="looking",
        title="Opening the desk",
        detail=(
            "Verifying the installed Diony AI application."
            if is_frozen()
            else "Looking for Python 3.11."
        ),
    )
    server = serve()
    url = lobby_url(server.server_address[1])
    print(f"Diony AI — Studio will open at {STUDIO_URL}", flush=True)
    print("A browser page will open. Leave this window open until it says you can close it.", flush=True)
    worker = threading.Thread(target=work, name="diony-desk-work", daemon=True)
    worker.start()
    already = False
    try:
        already = desk_already_installed()
    except Exception:
        already = False
    lobby_opened = should_open_lobby(already_installed=already, frozen=is_frozen())
    if lobby_opened:
        open_browser(url)
    worker.join()
    phase = public_state()["phase"]
    if should_open_studio_after_work(lobby_opened=lobby_opened, phase=phase):
        open_browser(public_state()["studio"] or STUDIO_URL)
        print(f"Studio is open at {STUDIO_URL}. You can close this window.", flush=True)
        print(f"Producer is {PRODUCER_URL} after you Sign.", flush=True)
    elif phase == "ready":
        print(f"Studio is opening here at {STUDIO_URL}. You can close this window.", flush=True)
        print(f"Producer is {PRODUCER_URL} after you Sign.", flush=True)
    elif phase == "need_names":
        if not lobby_opened:
            open_browser(url)
        print("Approve the password so http://studio.diony.local works.", flush=True)
        print("Leave this window open. Continue on the lobby retries.", flush=True)
    elif phase == "need_python":
        if not lobby_opened:
            open_browser(url)
        print("Install Python, then double-click Open Diony AI again.", flush=True)
    elif phase == "error":
        if not lobby_opened:
            open_browser(url)
        print(public_state()["detail"], flush=True)
        print("Leave this window open, or close it and double-click Open Diony AI again.", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
