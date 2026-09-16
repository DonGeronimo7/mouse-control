"""Regression coverage for path-independent discovery profile persistence."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mouse_control import device_profiles
from mouse_control.device_profiles import DeviceProfileStore, result_to_profile, validate_profile
from mouse_control.discovery_models import (
    DeviceNode,
    DiscoveredCapability,
    DiscoveryEvidence,
    DiscoveryResult,
    EvidenceLevel,
    PhysicalDevice,
    ProtocolMatch,
)


def _node(path: str, kind: str) -> DeviceNode:
    return DeviceNode(
        path=Path(path),
        sysfs_path=None,
        subsystem=kind,
        node_type=kind,
        bus=3,
        vendor_id=0x046D,
        product_id=0x4074,
        interface_number=2,
        name="G305",
        descriptor_sha256="descriptor-hash",
        parent_key="parent:test",
    )


def test_profile_redacts_live_paths_from_diagnostic_evidence(tmp_path):
    hidraw = _node("/dev/hidraw8", "hidraw")
    evdev = _node("/dev/input/event13", "evdev")
    physical = PhysicalDevice(
        "G305",
        0x046D,
        0x4074,
        3,
        None,
        [evdev],
        [hidraw],
        "model-hash",
        None,
    )
    protocol = ProtocolMatch(
        "hidpp2",
        "4.2",
        hidraw,
        evidence=[
            DiscoveryEvidence(
                EvidenceLevel.PROVEN,
                "hidpp2-single-responder",
                "Responder verified on /dev/hidraw8",
                source="protocol_discovery",
                details={
                    "failed_candidates": (
                        "hidraw6: timeout while reading /dev/hidraw6",
                        "hidraw7: no reply from /dev/hidraw7",
                    )
                },
            )
        ],
    )
    result = DiscoveryResult(
        physical,
        protocol,
        {
            "dpi": DiscoveredCapability(
                "dpi",
                readable=True,
                writable=True,
                minimum=200,
                maximum=12000,
                step=50,
                evidence=[
                    DiscoveryEvidence(
                        EvidenceLevel.PROVEN,
                        "hidpp-adjustable-dpi",
                        "Validated DPI readback through /dev/hidraw8",
                    )
                ],
            )
        },
        observations=[
            DiscoveryEvidence(
                EvidenceLevel.OBSERVED,
                "live-input",
                "Selected /dev/input/event13 during discovery",
                details={"path": "/dev/hidraw8"},
            )
        ],
    )

    profile = result_to_profile(result)
    serialized = json.dumps(profile, sort_keys=True)

    assert "/dev/hidraw" not in serialized
    assert "/dev/input/event" not in serialized
    assert "<live-hidraw>" in serialized
    assert "<live-input-event>" in serialized
    validate_profile(profile)

    path = DeviceProfileStore(tmp_path).save(result)
    assert path.exists()
    saved = path.read_text(encoding="utf-8")
    assert "/dev/hidraw" not in saved
    assert "/dev/input/event" not in saved


def test_sudo_profile_directory_uses_invoking_users_home(monkeypatch, tmp_path):
    """Privileged discovery must not cache learned profiles below /root."""

    monkeypatch.setattr(device_profiles.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setenv("SUDO_GID", "1000")
    monkeypatch.delenv("MOUSE_CONTROL_PROFILE_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(
        device_profiles.pwd,
        "getpwuid",
        lambda uid: SimpleNamespace(pw_dir=str(tmp_path)) if uid == 1000 else None,
    )

    assert device_profiles.get_profile_directory() == (
        tmp_path / ".local" / "share" / "mouse-control" / "devices"
    )
