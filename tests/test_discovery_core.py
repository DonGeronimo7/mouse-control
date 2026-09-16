from pathlib import Path
from types import SimpleNamespace
import json
import pytest

from mouse_control.discovery import MouseDevice
from mouse_control.discovery_models import (
    DeviceNode, DiscoveredCapability, DiscoveryEvidence, DiscoveryResult,
    EvidenceLevel, PhysicalDevice, ProtocolMatch,
)
from mouse_control.device_topology import (
    build_device_graph, calculate_instance_fingerprint, calculate_model_fingerprint,
)
from mouse_control.hid_descriptor import parse_report_descriptor
from mouse_control.event_correlation import (
    PhysicalAction, diff_feature_snapshots, detect_repeated_changes,
)
from mouse_control.device_profiles import DeviceProfileError, DeviceProfileStore, result_to_profile, validate_profile
from mouse_control.protocol_discovery import Hidpp20Detector
from mouse_control.discovery_engine import DiscoveryEngine
from mouse_control.discovery_ui import render_discovery_result, result_to_dict


def node(path, kind, parent='parent:usb-1', interface=2, descriptor='abc', uniq=''):
    return DeviceNode(
        path=Path(path), sysfs_path=None, subsystem=kind, node_type=kind,
        bus=3, vendor_id=0x046D, product_id=0x4074,
        interface_number=interface, name='G305', phys='usb-test/input2', uniq=uniq,
        descriptor_sha256=descriptor, parent_key=parent,
    )


def test_capability_write_requires_proven_evidence():
    cap = DiscoveredCapability('dpi', readable=True, writable=True, evidence=[
        DiscoveryEvidence(EvidenceLevel.CORRELATED, 'x', 'changed with button')
    ])
    assert cap.normalized().writable is False
    proven = DiscoveredCapability('dpi', readable=True, writable=True, evidence=[
        DiscoveryEvidence(EvidenceLevel.PROVEN, 'x', 'validated protocol')
    ])
    assert proven.normalized().writable is True


def test_descriptor_parser_tracks_input_and_feature_lengths():
    raw = bytes.fromhex(
        '05 01 09 02 A1 01 85 01 05 09 19 01 29 03 15 00 25 01 '
        '95 03 75 01 81 02 95 01 75 05 81 01 05 01 09 30 09 31 '
        '15 81 25 7F 75 08 95 02 81 06 C0 '
        '06 00 FF 85 05 75 08 95 04 B1 02'
    )
    parsed = parse_report_descriptor(raw)
    inputs = {r.report_id: r for r in parsed.input_reports}
    features = {r.report_id: r for r in parsed.feature_reports}
    assert inputs[1].byte_length == 4
    assert features[5].byte_length == 5
    assert 0xFF00 in features[5].usage_pages


def test_model_fingerprint_ignores_volatile_paths():
    a = [node('/dev/hidraw6', 'hidraw'), node('/dev/input/event13', 'evdev')]
    b = [node('/dev/hidraw14', 'hidraw'), node('/dev/input/event21', 'evdev')]
    assert calculate_model_fingerprint(a) == calculate_model_fingerprint(b)


def test_instance_fingerprint_does_not_treat_usb_port_as_device_identity():
    first = node('/dev/hidraw8', 'hidraw', parent='parent:/sys/devices/usb1/1-2', uniq='')
    second = node('/dev/hidraw9', 'hidraw', parent='parent:/sys/devices/usb1/1-7', uniq='')
    assert calculate_instance_fingerprint([first]) is None
    assert calculate_instance_fingerprint([second]) is None

    unique_a = node('/dev/hidraw8', 'hidraw', parent='parent:/sys/devices/usb1/1-2', uniq='serial-abc')
    unique_b = node('/dev/hidraw9', 'hidraw', parent='parent:/sys/devices/usb1/1-7', uniq='serial-abc')
    assert calculate_instance_fingerprint([unique_a]) == calculate_instance_fingerprint([unique_b])


def test_build_device_graph_anchors_selected_event():
    ev = node('/dev/input/event13', 'evdev')
    hid = node('/dev/hidraw8', 'hidraw')
    mouse = MouseDevice('G305', '/dev/input/event13', 'usb-test/input2', 0x046D, 0x4074, 3)
    physical = build_device_graph(mouse, evdev_nodes=[ev], hidraw_nodes=[hid])
    assert not physical.ambiguous
    assert physical.hidraw_nodes == [hid]


def test_multiple_matching_devices_become_ambiguous():
    ev1 = node('/dev/input/event13', 'evdev', parent='parent:usb-1')
    hid1 = node('/dev/hidraw8', 'hidraw', parent='parent:usb-1')
    ev2 = node('/dev/input/event14', 'evdev', parent='parent:usb-2')
    hid2 = node('/dev/hidraw9', 'hidraw', parent='parent:usb-2')
    mouse = MouseDevice('G305', '/missing/by-id', '', 0x046D, 0x4074, 3)
    physical = build_device_graph(mouse, evdev_nodes=[ev1, ev2], hidraw_nodes=[hid1, hid2])
    assert physical.ambiguous
    assert len(physical.hidraw_nodes) == 2


def test_feature_diff_and_repeated_change_detection():
    a1 = PhysicalAction(1, 2, feature_changes=diff_feature_snapshots({5: b'\x05\x00\x02'}, {5: b'\x05\x00\x03'}))
    a2 = PhysicalAction(3, 4, feature_changes=diff_feature_snapshots({5: b'\x05\x00\x03'}, {5: b'\x05\x00\x04'}))
    candidates = detect_repeated_changes([a1, a2])
    assert len(candidates) == 1
    assert candidates[0].report_key == 5
    assert candidates[0].offset == 2
    assert candidates[0].values == (2, 3, 4)


def test_profile_never_persists_device_paths(tmp_path):
    physical = PhysicalDevice('G305', 0x046D, 0x4074, 3, None,
        [node('/dev/input/event13', 'evdev')], [node('/dev/hidraw8', 'hidraw')],
        'modelhash', 'instancehash')
    protocol = ProtocolMatch('hidpp2', '4.2', physical.hidraw_nodes[0], [
        DiscoveryEvidence(EvidenceLevel.PROVEN, 'p', 'protocol')
    ])
    result = DiscoveryResult(physical, protocol, {'dpi': DiscoveredCapability(
        'dpi', True, True, minimum=200, maximum=12000, step=50,
        evidence=[DiscoveryEvidence(EvidenceLevel.PROVEN, 'dpi', 'proven')]
    )})
    profile = result_to_profile(result)
    text = json.dumps(profile)
    assert '/dev/hidraw' not in text
    assert '/dev/input/event' not in text
    validate_profile(profile)
    path = DeviceProfileStore(tmp_path).save(result)
    assert path.exists()


def test_profile_rejects_unproven_write():
    profile = {
        'schema_version': 1,
        'identity': {'vendor_id': 1, 'product_id': 2, 'bus': 3},
        'fingerprints': {'model': 'x', 'instance': None},
        'capabilities': {'dpi': {'writable': True, 'evidence': []}},
    }
    with pytest.raises(DeviceProfileError):
        validate_profile(profile)


def test_hidpp_detector_requires_one_responder():
    good = node('/dev/hidraw8', 'hidraw')
    physical = PhysicalDevice('G305', 0x046D, 0x4074, 3, None, [], [good], 'm', 'i')

    sessions = []
    class Session:
        def __init__(self, path): self.path=path; self.closed=False; sessions.append(self)
        def close(self): self.closed=True

    dpi = SimpleNamespace(readable=True, writable=True, values=(), ranges=(SimpleNamespace(minimum=200, maximum=12000, step=50),))
    rr = SimpleNamespace(readable=True, writable=True, values=(125,250,500,1000))
    batt = SimpleNamespace(readable=True)
    driver = SimpleNamespace(
        protocol_version=(4,2), device_index=1, name='G305', features={0x2201: object(), 0x8060: object()},
        capabilities=SimpleNamespace(dpi=dpi, report_rate=rr, battery=batt),
    )
    detector = Hidpp20Detector(session_factory=Session, connector=lambda s: driver)
    match = detector.detect(physical)
    assert match is not None and match.name == 'hidpp2'
    assert match.metadata['capabilities']['dpi'].writable
    assert all(s.closed for s in sessions)


def test_engine_unknown_device_creates_no_writable_capability(tmp_path):
    physical = PhysicalDevice('Unknown', 0x1234, 0x5678, 3, None, [], [], 'm', None)
    mouse = MouseDevice('Unknown', '/dev/input/event1', '', 0x1234, 0x5678, 3)
    engine = DiscoveryEngine(
        topology_builder=lambda _mouse: physical,
        detectors=(),
        profile_store=DeviceProfileStore(tmp_path),
        save_profiles=False,
    )
    result = engine.discover(mouse)
    assert result.protocol is None
    assert not result.writable
    assert result.capabilities == {}


def test_engine_exposes_saved_profile_path(tmp_path):
    physical = PhysicalDevice('Unknown', 0x1234, 0x5678, 3, None, [], [], 'model-hash', None)
    mouse = MouseDevice('Unknown', '/dev/input/event1', '', 0x1234, 0x5678, 3)
    engine = DiscoveryEngine(
        topology_builder=lambda _mouse: physical,
        detectors=(),
        profile_store=DeviceProfileStore(tmp_path),
        save_profiles=True,
    )
    result = engine.discover(mouse)
    assert engine.profile_path is not None
    assert engine.profile_path.exists()
    rendered = render_discovery_result(result, profile_path=engine.profile_path)
    assert 'unknown / not yet validated' in rendered
    payload = result_to_dict(result, profile_path=engine.profile_path)
    assert payload['profile_path'] == str(engine.profile_path)
    assert payload['device']['ambiguous'] is False
