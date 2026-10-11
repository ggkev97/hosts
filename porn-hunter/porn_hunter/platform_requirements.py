"""Pick the requirements file that has wheels for this computer.

The newest PyTorch, torchvision and faiss-cpu only publish builds for Apple-silicon Macs on
macOS 14+, and PyTorch stopped publishing Intel-Mac builds after 2.2.2. start.sh / start.bat call
`python -m porn_hunter.platform_requirements` and install whatever file it names.
Standard library only, so it runs before anything is installed.
"""
from __future__ import annotations

import platform
import sys

DEFAULT = "requirements.txt"
MACOS_APPLE_LEGACY = "requirements-macos-legacy.txt"   # Apple silicon, macOS < 14
MACOS_INTEL = "requirements-macos-intel.txt"           # any Intel Mac (or Python running under Rosetta)


def choose(system: str, machine: str, mac_version: str = "") -> str:
    if system != "Darwin":
        return DEFAULT
    if machine.lower() in ("x86_64", "amd64", "i386"):
        return MACOS_INTEL
    try:
        major = int(mac_version.split(".")[0])
    except (ValueError, IndexError):
        return DEFAULT
    return MACOS_APPLE_LEGACY if major < 14 else DEFAULT


def describe() -> str:
    """One line a user can copy to me when something fails."""
    return (f"{platform.system()} {platform.release()} | machine={platform.machine()} | "
            f"macOS={platform.mac_ver()[0] or 'n/a'} | python={platform.python_version()}")


if __name__ == "__main__":
    if "--describe" in sys.argv:
        print(describe())
    else:
        print(choose(platform.system(), platform.machine(), platform.mac_ver()[0]))
