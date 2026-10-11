import re
import subprocess
import sys
from pathlib import Path

import pytest

from porn_hunter.platform_requirements import (DEFAULT, MACOS_APPLE_LEGACY, MACOS_INTEL, choose,
                                               describe)

ROOT = Path(__file__).resolve().parent.parent
FILES = [DEFAULT, MACOS_APPLE_LEGACY, MACOS_INTEL]
PLATFORM_SPECIFIC = {"torch", "torchvision", "faiss-cpu", "numpy"}


@pytest.mark.parametrize("system, machine, mac, expected", [
    ("Darwin", "arm64", "14.5", DEFAULT),
    ("Darwin", "arm64", "15.1", DEFAULT),
    ("Darwin", "arm64", "13.6", MACOS_APPLE_LEGACY),
    ("Darwin", "arm64", "12.7.6", MACOS_APPLE_LEGACY),
    ("Darwin", "arm64", "11.7", MACOS_APPLE_LEGACY),
    ("Darwin", "arm64", "10.16", MACOS_APPLE_LEGACY),      # old-SDK Python reporting 10.16
    ("Darwin", "x86_64", "12.7.6", MACOS_INTEL),
    ("Darwin", "x86_64", "15.0", MACOS_INTEL),             # Intel never gets new PyTorch
    ("Darwin", "arm64", "", DEFAULT),                      # unknown version: don't guess
    ("Windows", "AMD64", "", DEFAULT),
    ("Linux", "x86_64", "", DEFAULT),
    ("Linux", "aarch64", "", DEFAULT),
])
def test_choose(system, machine, mac, expected):
    assert choose(system, machine, mac) == expected


def parse(name):
    pins = {}
    for line in (ROOT / name).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
            assert m, f"{name}: {line!r} is not an exact pin"
            pins[m.group(1).lower()] = m.group(2)
    return pins


@pytest.mark.parametrize("name", FILES)
def test_every_requirement_is_exactly_pinned(name):
    assert len(parse(name)) >= 14


def test_variants_differ_from_default_only_in_platform_specific_packages():
    default = parse(DEFAULT)
    for name in (MACOS_APPLE_LEGACY, MACOS_INTEL):
        other = parse(name)
        assert set(other) == set(default), f"{name} has different packages"
        changed = {p for p in default if default[p] != other[p]}
        assert changed <= PLATFORM_SPECIFIC, f"{name} changes {changed - PLATFORM_SPECIFIC}"


def test_old_torch_stacks_are_consistent():
    """torchvision pins its torch exactly; torch 2.2.x cannot use NumPy 2."""
    intel, legacy = parse(MACOS_INTEL), parse(MACOS_APPLE_LEGACY)
    assert (intel["torch"], intel["torchvision"]) == ("2.2.2", "0.17.2")
    assert int(intel["numpy"].split(".")[0]) < 2
    assert (legacy["torch"], legacy["torchvision"]) == ("2.11.0", "0.26.0")


def test_cli_prints_a_known_file_and_a_description():
    out = subprocess.run([sys.executable, "-m", "porn_hunter.platform_requirements"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out in FILES and (ROOT / out).is_file()
    desc = subprocess.run([sys.executable, "-m", "porn_hunter.platform_requirements", "--describe"],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    assert "machine=" in desc and desc == describe()
