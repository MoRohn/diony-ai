"""ffmpeg comes with Diony.

The desk owns the media tools the Director and Producer shell out to. On
every open it looks for ffmpeg and ffprobe — an explicit ``DIONY_FFMPEG`` /
``DIONY_FFPROBE``, the copies packaged inside the application, the copies
it installed on an earlier open, then the shell PATH and the usual homes —
and when none answers it installs the release pinned in ``ffmpeg_lock.json``
into the user's data folder, SHA-256 verified. Nothing here needs a package
manager, a terminal, or a password; the paths it finds are exported so every
service the desk starts inherits them.

``python -m diony_desk.media fetch <platform-key> <out_dir>`` stages the same
pinned release for the desktop build, which packages it beside the app.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.request import Request, urlopen

LOCK_PATH = Path(__file__).with_name("ffmpeg_lock.json")
TOOLS = ("ffmpeg", "ffprobe")
PLATFORM_KEYS = ("macos-arm64", "macos-amd64", "windows-amd64")
Progress = Callable[[int, int], None]
Fetch = Callable[[str, Path, Progress], None]
Say = Callable[[str], object]


def platform_key(platform_name: str | None = None, machine: str | None = None) -> str:
    """``macos-arm64``, ``macos-amd64``, ``windows-amd64``, ``linux-…``."""
    platform_name = platform_name or sys.platform
    machine = (machine or platform.machine() or "").lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    if platform_name == "darwin":
        return f"macos-{arch}"
    if platform_name.startswith("win") or platform_name == "nt":
        return f"windows-{arch}"
    return f"linux-{arch}"


def is_windows(platform_name: str | None = None) -> bool:
    platform_name = platform_name or sys.platform
    return platform_name.startswith("win") or platform_name == "nt"


def tool_filename(name: str, platform_name: str | None = None) -> str:
    return f"{name}.exe" if is_windows(platform_name) else name


def read_lock(path: Path = LOCK_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pinned(key: str, lock: dict | None = None) -> dict | None:
    lock = lock or read_lock()
    entry = lock.get("platforms", {}).get(key)
    return entry if isinstance(entry, dict) and entry.get("artifacts") else None


def bundled_dirs() -> tuple[Path, ...]:
    """Where the application build placed ffmpeg: the PyInstaller runtime folder
    (``Contents/Frameworks`` inside Diony.app, the app folder on Windows) and
    the executable's own folder; ``DIONY_FFMPEG_BUNDLE_DIR`` names one outright."""
    explicit = os.environ.get("DIONY_FFMPEG_BUNDLE_DIR", "").strip()
    if explicit:
        return (Path(explicit).expanduser(),)
    homes = []
    runtime = getattr(sys, "_MEIPASS", "")
    if runtime:
        homes.append(Path(str(runtime)))
    homes.append(Path(sys.executable).resolve().parent)
    return tuple(homes)


def tools_dir(data_dir: Path) -> Path:
    """Where the desk installs ffmpeg when the bundle lacks it."""
    return data_dir / "tools" / "ffmpeg"


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _known_dirs(platform_name: str | None = None) -> tuple[Path, ...]:
    env = os.environ
    if is_windows(platform_name):
        homes: list[Path] = []
        if env.get("LOCALAPPDATA"):
            homes.append(Path(env["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links")
            homes.append(Path(env["LOCALAPPDATA"]) / "Programs" / "ffmpeg" / "bin")
        if env.get("ChocolateyInstall"):
            homes.append(Path(env["ChocolateyInstall"]) / "bin")
        if env.get("USERPROFILE"):
            homes.append(Path(env["USERPROFILE"]) / "scoop" / "shims")
        for root in (env.get("ProgramFiles"), env.get("ProgramFiles(x86)")):
            if root:
                homes.append(Path(root) / "ffmpeg" / "bin")
        homes.append(Path(r"C:\ffmpeg\bin"))
        return tuple(homes)
    return (Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin"))


def find_tool(name: str, *, data_dir: Path, platform_name: str | None = None) -> tuple[str, str]:
    """The tool's path and where it came from: configured, bundled, installed,
    system, or missing."""
    env_name = "DIONY_FFMPEG" if name == "ffmpeg" else "DIONY_FFPROBE"
    configured = os.environ.get(env_name, "").strip()
    if configured:
        resolved = shutil.which(configured) or (configured if Path(configured).is_file() else "")
        if resolved:
            return resolved, "configured"
    filename = tool_filename(name, platform_name)
    homes = [(folder, "bundled") for folder in bundled_dirs()]
    homes.append((tools_dir(data_dir), "installed"))
    for folder, source in homes:
        candidate = folder / filename
        if _runnable(candidate):
            return str(candidate), source
    resolved = shutil.which(name)
    if resolved:
        return resolved, "system"
    for folder in _known_dirs(platform_name):
        candidate = folder / filename
        if _runnable(candidate):
            return str(candidate), "system"
    return "", "missing"


def version_of(path: str) -> str:
    """``9.0.1`` from ``ffmpeg -version``; empty when the tool will not answer."""
    if not path:
        return ""
    try:
        out = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    first = (out.stdout or "").splitlines()[0] if out.stdout else ""
    parts = first.split()
    if len(parts) >= 3 and parts[1] == "version":
        return parts[2].split("-")[0]
    return ""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_fetch(url: str, dest: Path, progress: Progress) -> None:
    request = Request(url, headers={"User-Agent": "Diony AI desk"})
    with urlopen(request, timeout=60) as response, dest.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        for chunk in iter(lambda: response.read(1 << 20), b""):
            out.write(chunk)
            done += len(chunk)
            progress(done, total)


def cached_fetch(cache: Path, inner: Fetch = default_fetch) -> Fetch:
    """A fetch that keeps every archive under ``cache`` and reuses it when its
    SHA-256 still matches the lock — the build machine downloads once."""

    def fetch(url: str, dest: Path, progress: Progress) -> None:
        cache.mkdir(parents=True, exist_ok=True)
        kept = cache / Path(url).name
        expected = _expected_sha(url)
        if not (kept.is_file() and expected and sha256_of(kept) == expected):
            inner(url, kept, progress)
        shutil.copyfile(kept, dest)

    return fetch


def _expected_sha(url: str) -> str:
    for entry in read_lock().get("platforms", {}).values():
        for artifact in entry.get("artifacts", []):
            if artifact.get("url") == url:
                return str(artifact.get("sha256", ""))
    return ""


def install(
    entry: dict,
    target: Path,
    *,
    platform_name: str | None = None,
    fetch: Fetch | None = None,
    say: Say = print,
) -> dict[str, str]:
    """Download the entry's archives, verify each against its pinned SHA-256,
    and place the tools in ``target`` — all or nothing."""
    fetch = fetch or default_fetch
    target.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".ffmpeg-") as tmp:
        work = Path(tmp)
        for artifact in entry["artifacts"]:
            archive = work / Path(artifact["url"]).name
            last = {"pct": -1}

            def progress(done: int, total: int, _name: str = archive.name, _last=last) -> None:
                pct = int(done * 100 / total) if total else 0
                if pct // 10 != _last["pct"] // 10:
                    _last["pct"] = pct
                    say(f"Downloading {_name}: {pct}%")

            fetch(artifact["url"], archive, progress)
            digest = sha256_of(archive)
            if digest != artifact["sha256"]:
                raise RuntimeError(
                    f"{archive.name} did not match its pinned SHA-256 "
                    f"({digest[:12]}… vs {artifact['sha256'][:12]}…); nothing was installed"
                )
            with zipfile.ZipFile(archive) as bundle:
                for member, filename in artifact["members"].items():
                    out = work / filename
                    with bundle.open(member) as src, out.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    if not filename.lower().endswith(".txt"):
                        out.chmod(0o755)
                    staged[filename] = out
        for filename, path in staged.items():
            os.replace(path, target / filename)
    (target / "PINNED.json").write_text(
        json.dumps(
            {"version": entry["version"], "source": entry["source"], "license": entry["license"]},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {name: str(target / tool_filename(name, platform_name)) for name in TOOLS}


def manual_hint(key: str) -> str:
    if key.startswith("windows"):
        return "install it with `winget install Gyan.FFmpeg` or set DIONY_FFMPEG to ffmpeg.exe"
    if key.startswith("macos"):
        return "install it with `brew install ffmpeg` or set DIONY_FFMPEG to its path"
    return "install it with your package manager (sudo apt install ffmpeg) or set DIONY_FFMPEG"


def ensure_ffmpeg(
    *,
    data_dir: Path,
    say: Say = print,
    fetch: Fetch | None = None,
    platform_name: str | None = None,
    machine: str | None = None,
    allow_download: bool = True,
) -> dict:
    """Find or install ffmpeg + ffprobe and export their paths. Never raises."""
    key = platform_key(platform_name, machine)
    found = {
        name: find_tool(name, data_dir=data_dir, platform_name=platform_name) for name in TOOLS
    }
    result: dict = {
        "ready": False,
        "source": "missing",
        "platform": key,
        "ffmpeg": "",
        "ffprobe": "",
        "version": "",
        "note": "",
    }
    if all(path for path, _ in found.values()):
        result.update(
            ready=True,
            source=found["ffmpeg"][1],
            ffmpeg=found["ffmpeg"][0],
            ffprobe=found["ffprobe"][0],
        )
    else:
        entry = pinned(key)
        if entry is None or not allow_download:
            result["note"] = f"ffmpeg is not on this desk — {manual_hint(key)}"
            return result
        say(f"Installing ffmpeg {entry['version']} for this desk — a one-time download.")
        try:
            paths = install(
                entry, tools_dir(data_dir), platform_name=platform_name, fetch=fetch, say=say
            )
        except Exception as exc:
            result["note"] = f"ffmpeg install failed: {str(exc)[:200]} — {manual_hint(key)}"
            say(result["note"])
            return result
        result.update(
            ready=True, source="installed", ffmpeg=paths["ffmpeg"], ffprobe=paths["ffprobe"]
        )
    os.environ["DIONY_FFMPEG"] = result["ffmpeg"]
    os.environ["DIONY_FFPROBE"] = result["ffprobe"]
    result["version"] = version_of(result["ffmpeg"])
    label = {
        "bundled": "ships with Diony",
        "installed": "installed by Diony",
        "system": "found on this desk",
        "configured": "set by DIONY_FFMPEG",
    }[result["source"]]
    say(f"ffmpeg {result['version']} ready — {label}.".replace("ffmpeg  ", "ffmpeg "))
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == "fetch":
        key, out = argv[1], Path(argv[2])
        entry = pinned(key)
        if entry is None:
            print(f"no pinned ffmpeg for {key}; choose one of {PLATFORM_KEYS}", file=sys.stderr)
            return 2
        cache = (
            Path(
                os.environ.get("DIONY_FFMPEG_CACHE", "") or Path.home() / ".diony-build" / "ffmpeg"
            )
            / key
        )
        platform_name = (
            "win32"
            if key.startswith("windows")
            else "darwin"
            if key.startswith("macos")
            else "linux"
        )
        paths = install(entry, out, platform_name=platform_name, fetch=cached_fetch(cache))
        print(json.dumps({"platform": key, "version": entry["version"], **paths}))
        return 0
    print("usage: python -m diony_desk.media fetch <platform-key> <out_dir>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
