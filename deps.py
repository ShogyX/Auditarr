"""
deps.py — Check that all dependencies are available on first run.

If a dependency is missing, surface a clear error to stderr (and to the
frontend via /api/health) telling the user:
  - what's missing
  - the command to install it
  - where Auditarr expects to find it
"""
import os
import shutil
import subprocess
import sys


# ─── What we need ────────────────────────────────────────────────────────────

# Python packages (import-name → pip-name)
PYTHON_PACKAGES = {
    "flask": "flask",
    "apscheduler": "apscheduler",
}

# External binaries (cmd-name → install hints per platform)
BINARIES = {
    "ffprobe": {
        "description": "Reads media file metadata. Bundled with ffmpeg.",
        "expected_path": "anywhere on PATH",
        "install": {
            "debian":  "sudo apt-get install -y ffmpeg",
            "fedora":  "sudo dnf install -y ffmpeg",
            "arch":    "sudo pacman -S --noconfirm ffmpeg",
            "alpine":  "sudo apk add --no-cache ffmpeg",
            "macos":   "brew install ffmpeg",
            "windows": "winget install ffmpeg   (or download from https://ffmpeg.org)",
            "generic": "Install ffmpeg from your package manager or https://ffmpeg.org",
        },
    },
    "ffmpeg": {
        "description": "Used for hashing / sample-frame extraction.",
        "expected_path": "anywhere on PATH",
        "install": {
            "debian":  "sudo apt-get install -y ffmpeg",
            "fedora":  "sudo dnf install -y ffmpeg",
            "arch":    "sudo pacman -S --noconfirm ffmpeg",
            "alpine":  "sudo apk add --no-cache ffmpeg",
            "macos":   "brew install ffmpeg",
            "windows": "winget install ffmpeg",
            "generic": "Install ffmpeg from your package manager or https://ffmpeg.org",
        },
    },
}


def _detect_distro() -> str:
    """Return one of: debian, fedora, arch, alpine, macos, windows, generic."""
    if sys.platform == "darwin":  return "macos"
    if sys.platform == "win32":   return "windows"
    if not os.path.exists("/etc/os-release"): return "generic"
    try:
        with open("/etc/os-release") as f:
            data = {}
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    data[k] = v.strip('"')
        idl = (data.get("ID", "") + " " + data.get("ID_LIKE", "")).lower()
        if "debian" in idl or "ubuntu" in idl or "mint" in idl or "kali" in idl:
            return "debian"
        if "fedora" in idl or "rhel" in idl or "centos" in idl or "rocky" in idl or "alma" in idl:
            return "fedora"
        if "arch" in idl or "manjaro" in idl or "endeavour" in idl:
            return "arch"
        if "alpine" in idl: return "alpine"
    except Exception: pass
    return "generic"


def check_python_packages() -> list[dict]:
    """Return a list of missing package descriptors."""
    missing = []
    for import_name, pip_name in PYTHON_PACKAGES.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append({
                "type": "python",
                "name": import_name,
                "pip_name": pip_name,
                "install_cmd": f"{sys.executable} -m pip install {pip_name}",
                "install_cmd_root": f"sudo {sys.executable} -m pip install {pip_name}",
                "expected_path": "the active Python's site-packages",
            })
    return missing


def check_binaries() -> list[dict]:
    """Return a list of missing binary descriptors."""
    distro = _detect_distro()
    missing = []
    for binary, meta in BINARIES.items():
        path = shutil.which(binary)
        if not path:
            missing.append({
                "type": "binary",
                "name": binary,
                "description": meta["description"],
                "distro": distro,
                "install_cmd": meta["install"].get(distro, meta["install"]["generic"]),
                "install_cmd_generic": meta["install"]["generic"],
                "expected_path": meta["expected_path"],
            })
    return missing


def install_python_packages(missing: list[dict], use_sudo: bool = True) -> tuple[bool, str]:
    """Try to pip-install missing Python packages. Returns (ok, log)."""
    if not missing: return True, "Nothing to install"
    pip_names = [m["pip_name"] for m in missing if m["type"] == "python"]
    if not pip_names: return True, "No Python packages to install"
    cmd = [sys.executable, "-m", "pip", "install"] + pip_names
    if use_sudo and os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo", "-n"] + cmd
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        ok = (out.returncode == 0)
        return ok, (out.stdout + out.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return False, "pip install timed out after 120s"
    except Exception as e:
        return False, str(e)


def check_all() -> dict:
    """Return a complete dependency report."""
    missing_py = check_python_packages()
    missing_bin = check_binaries()
    return {
        "ok": not (missing_py or missing_bin),
        "python_missing": missing_py,
        "binary_missing": missing_bin,
        "python_executable": sys.executable,
        "platform": sys.platform,
        "distro": _detect_distro(),
    }


def format_report(report: dict) -> str:
    """Human-friendly text report for stderr."""
    if report["ok"]:
        return "✓ All dependencies present."

    lines = []
    lines.append("=" * 64)
    lines.append("Auditarr — missing dependencies")
    lines.append("=" * 64)
    lines.append("")
    if report["python_missing"]:
        lines.append("Python packages required:")
        for m in report["python_missing"]:
            lines.append(f"  · {m['name']:<20} (pip: {m['pip_name']})")
            lines.append(f"        Expected at: {m['expected_path']}")
            lines.append(f"        Install:     {m['install_cmd']}")
            lines.append(f"        With sudo:   {m['install_cmd_root']}")
            lines.append("")
    if report["binary_missing"]:
        lines.append("External binaries required:")
        for m in report["binary_missing"]:
            lines.append(f"  · {m['name']}")
            lines.append(f"        {m['description']}")
            lines.append(f"        Expected at:  {m['expected_path']}")
            lines.append(f"        Install:      {m['install_cmd']}")
            lines.append("")
    lines.append("Detected platform: " + report["platform"] +
                 (f" ({report['distro']})" if report["distro"] != "generic" else ""))
    lines.append("")
    lines.append("To attempt automatic installation of Python packages, run:")
    lines.append(f"  {sys.executable} server.py --install-deps")
    lines.append("")
    lines.append("=" * 64)
    return "\n".join(lines)


def enforce_or_exit(allow_missing_binaries: bool = False):
    """Run at startup. If anything is missing, print the report and exit.

    If allow_missing_binaries=True, the app continues even with missing
    binaries (degraded mode — features that need them will fail per-call).
    """
    report = check_all()
    if report["ok"]:
        return report
    print(format_report(report), file=sys.stderr)
    if report["python_missing"]:
        # Python packages are non-negotiable — nothing imports without them.
        sys.exit(2)
    if report["binary_missing"] and not allow_missing_binaries:
        # Still allow startup; just warn loudly.
        print("\n[deps] Continuing without external binaries — scans will fail "
              "until they're installed.\n", file=sys.stderr)
    return report
