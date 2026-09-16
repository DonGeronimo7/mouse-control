from pathlib import Path
from unittest.mock import patch

from mouse_control import cli
from mouse_control.discovery import MouseDevice
from mouse_control.generic_hid import discover_hid_devices


def _hidraw(root: Path, name: str, identity: str, descriptor: bytes,
            *, hid_name: str = "Test mouse") -> None:
    device = root / name / "device"
    device.mkdir(parents=True)
    (device / "uevent").write_text(
        f"HID_ID={identity}\nHID_NAME={hid_name}\nHID_PHYS=usb-test/input0\n"
    )
    (device / "report_descriptor").write_bytes(descriptor)


def test_generic_hid_discovery_matches_exact_identity_and_keeps_interfaces(tmp_path):
    sysfs, dev = tmp_path / "sys", tmp_path / "dev"
    _hidraw(sysfs, "hidraw0", "0003:0000046D:00004074", b"\x05\x01")
    _hidraw(sysfs, "hidraw1", "0003:0000046D:00004074", b"\x06\x00\xff")
    _hidraw(sysfs, "hidraw2", "0003:00001234:00005678", b"wrong")
    mouse = MouseDevice("G305", "/dev/input/mouse", vendor=0x046d, product=0x4074,
                        bustype=0x0003)

    found = discover_hid_devices(mouse, sysfs=sysfs, dev_root=dev)

    assert [item.path for item in found] == [dev / "hidraw0", dev / "hidraw1"]
    assert [item.report_descriptor for item in found] == [b"\x05\x01", b"\x06\x00\xff"]


def test_generic_hid_discovery_requires_usb_identity(tmp_path):
    assert discover_hid_devices(MouseDevice("unknown", "/test"), sysfs=tmp_path) == []
    assert discover_hid_devices(
        MouseDevice("missing transport", "/test", vendor=0x046d, product=0x4074),
        sysfs=tmp_path) == []


def test_generic_hid_discovery_respects_bluetooth_transport(tmp_path):
    sysfs = tmp_path / "sys"
    _hidraw(sysfs, "hidraw0", "0005:00001234:00005678", b"bluetooth")
    mouse = MouseDevice("F33", "/dev/input/mouse", vendor=0x1234, product=0x5678,
                        bustype=0x0005)

    assert [item.report_descriptor for item in
            discover_hid_devices(mouse, sysfs=sysfs)] == [b"bluetooth"]


def test_debug_hid_rejects_unbounded_or_invalid_duration(capsys):
    assert cli.debug_hid(0) == 2
    assert "greater than zero" in capsys.readouterr().err


def test_debug_hid_is_read_only_and_reports_unique_inputs(capsys):
    mouse = MouseDevice("F33", "/dev/input/test", vendor=0x1234, product=0x5678)
    interface = type("Interface", (), {
        "path": Path("/dev/hidraw9"), "name": "F33", "phys": "usb-test/input1",
        "report_descriptor": b"\x06\x00\xff",
    })()

    def capture(_interface, _seconds, callback):
        callback(b"\x01\x02")
        callback(b"\x01\x02")

    with patch.object(cli, "get_mouse_devices", return_value=[mouse]), \
         patch.object(cli, "select_mouse_device", return_value=mouse), \
         patch.object(cli, "discover_hid_devices", return_value=[interface]), \
         patch.object(cli, "capture_input_reports", side_effect=capture):
        assert cli.debug_hid(1) == 0

    output = capsys.readouterr().out
    assert "1234:5678" in output
    assert output.count("input: 01 02") == 1
    assert "No HID feature or output reports were sent." in output
