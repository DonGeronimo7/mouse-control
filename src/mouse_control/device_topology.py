"""Build a physical-device graph from evdev, hidraw, and sysfs.

The core rule is that volatile character-device names are interfaces, not
identities.  ``/dev/hidraw8`` and ``/dev/input/event13`` may change after a
replug; discovery therefore correlates them through sysfs ancestry, unique
identifiers, physical paths, descriptors, and VID/PID/bus facts.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Iterable, Sequence

from .discovery import MouseDevice
from .discovery_models import (
    DeviceNode,
    DiscoveryEvidence,
    EvidenceLevel,
    PhysicalDevice,
)


class TopologyError(RuntimeError):
    """Physical device topology could not be resolved safely."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_uevent(path: Path) -> dict[str, str]:
    text = _read_text(path)
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def _iter_ancestors(path: Path) -> Iterable[Path]:
    try:
        current = path.resolve()
    except OSError:
        current = path
    yield current
    yield from current.parents


def _find_ancestor_file(path: Path, filename: str) -> Path | None:
    for ancestor in _iter_ancestors(path):
        if (ancestor / filename).exists():
            return ancestor
    return None


def _parse_interface_number(path: Path) -> int | None:
    ancestor = _find_ancestor_file(path, "bInterfaceNumber")
    if ancestor is None:
        return None
    value = _read_text(ancestor / "bInterfaceNumber")
    try:
        return int(value, 16)
    except ValueError:
        return None


def _descriptor_bytes(path: Path) -> bytes:
    for ancestor in _iter_ancestors(path):
        descriptor = ancestor / "report_descriptor"
        if descriptor.is_file():
            try:
                return descriptor.read_bytes()
            except OSError:
                return b""
    return b""


def _descriptor_sha256(path: Path) -> str | None:
    descriptor = _descriptor_bytes(path)
    return hashlib.sha256(descriptor).hexdigest() if descriptor else None


def _parse_hid_id(value: str) -> tuple[int | None, int | None, int | None]:
    try:
        bus, vendor, product = value.split(":", 2)
        return int(bus, 16), int(vendor, 16), int(product, 16)
    except (ValueError, AttributeError):
        return None, None, None


def _normalized_phys(phys: str) -> str:
    # Interfaces of one composite HID commonly end in /input0, /input1, ...
    return re.sub(r"/input\d+(?:/.*)?$", "", phys or "")


def find_parent_device(sysfs_path: Path) -> Path | None:
    """Return the nearest stable physical transport parent when available."""

    # USB device directory: contains idVendor/idProduct but not merely a HID ID.
    for ancestor in _iter_ancestors(sysfs_path):
        if (ancestor / "idVendor").is_file() and (ancestor / "idProduct").is_file():
            return ancestor

    # Bluetooth and some virtual transports do not expose USB identity files.
    # Fall back to the nearest HID ancestor rather than inventing a path.
    for ancestor in _iter_ancestors(sysfs_path):
        if "HID_ID=" in _read_text(ancestor / "uevent"):
            return ancestor
    return None


def _parent_key(
    *,
    sysfs_path: Path,
    bus: int | None,
    vendor: int | None,
    product: int | None,
    phys: str,
    uniq: str,
) -> str:
    # Composite USB interfaces must group by the physical USB parent even when
    # only one child interface exposes HID_UNIQ.  Protocol discovery can then
    # disambiguate multiple logical HID++ responders safely.
    usb_parent = _find_ancestor_file(sysfs_path, "idVendor")
    if usb_parent is not None and (usb_parent / "idProduct").is_file():
        try:
            return f"parent:{usb_parent.resolve()}"
        except OSError:
            return f"parent:{usb_parent}"
    if uniq:
        return f"uniq:{bus}:{vendor}:{product}:{uniq}"
    parent = find_parent_device(sysfs_path)
    if parent is not None:
        try:
            return f"parent:{parent.resolve()}"
        except OSError:
            return f"parent:{parent}"
    normalized = _normalized_phys(phys)
    if normalized:
        return f"phys:{bus}:{vendor}:{product}:{normalized}"
    return f"identity:{bus}:{vendor}:{product}:{sysfs_path}"


def enumerate_evdev_nodes(
    *,
    sys_class_input: Path = Path("/sys/class/input"),
) -> list[DeviceNode]:
    """Enumerate evdev nodes without filtering away composite interfaces."""

    try:
        from evdev import InputDevice, list_devices
    except ImportError as exc:  # pragma: no cover - project dependency in production
        raise TopologyError("python-evdev is required for input discovery") from exc

    result: list[DeviceNode] = []
    for path_text in list_devices():
        path = Path(path_text)
        device = None
        try:
            device = InputDevice(path_text)
            info = getattr(device, "info", None)
            bus = getattr(info, "bustype", None)
            vendor = getattr(info, "vendor", None)
            product = getattr(info, "product", None)
            name = device.name or ""
            phys = getattr(device, "phys", "") or ""
            uniq = getattr(device, "uniq", "") or ""
        except (OSError, PermissionError):
            continue
        finally:
            if device is not None:
                device.close()

        sysfs = sys_class_input / path.name / "device"
        parent_key = _parent_key(
            sysfs_path=sysfs,
            bus=bus,
            vendor=vendor,
            product=product,
            phys=phys,
            uniq=uniq,
        )
        result.append(
            DeviceNode(
                path=path,
                sysfs_path=sysfs,
                subsystem="input",
                node_type="evdev",
                bus=bus,
                vendor_id=vendor,
                product_id=product,
                interface_number=_parse_interface_number(sysfs),
                name=name,
                phys=phys,
                uniq=uniq,
                descriptor_sha256=_descriptor_sha256(sysfs),
                parent_key=parent_key,
            )
        )
    return result


def enumerate_hidraw_nodes(
    *,
    sys_class_hidraw: Path = Path("/sys/class/hidraw"),
    dev_root: Path = Path("/dev"),
) -> list[DeviceNode]:
    """Enumerate every hidraw interface and retain composite siblings."""

    result: list[DeviceNode] = []
    for entry in sorted(sys_class_hidraw.glob("hidraw*")):
        sysfs = entry / "device"
        fields = _read_uevent(sysfs / "uevent")
        bus, vendor, product = _parse_hid_id(fields.get("HID_ID", ""))
        name = fields.get("HID_NAME", "")
        phys = fields.get("HID_PHYS", "")
        uniq = fields.get("HID_UNIQ", "")
        result.append(
            DeviceNode(
                path=dev_root / entry.name,
                sysfs_path=sysfs,
                subsystem="hidraw",
                node_type="hidraw",
                bus=bus,
                vendor_id=vendor,
                product_id=product,
                interface_number=_parse_interface_number(sysfs),
                name=name,
                phys=phys,
                uniq=uniq,
                descriptor_sha256=_descriptor_sha256(sysfs),
                parent_key=_parent_key(
                    sysfs_path=sysfs,
                    bus=bus,
                    vendor=vendor,
                    product=product,
                    phys=phys,
                    uniq=uniq,
                ),
            )
        )
    return result


def read_sysfs_identity(node: DeviceNode) -> dict[str, object]:
    """Return the identity facts discovery is allowed to use for correlation."""

    return {
        "bus": node.bus,
        "vendor_id": node.vendor_id,
        "product_id": node.product_id,
        "interface_number": node.interface_number,
        "phys": node.phys,
        "uniq": node.uniq,
        "descriptor_sha256": node.descriptor_sha256,
        "parent_key": node.parent_key,
    }


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calculate_model_fingerprint(nodes: Sequence[DeviceNode]) -> str:
    """Fingerprint a model/protocol layout without volatile node paths."""

    identities = sorted(
        (
            (
                node.bus,
                node.vendor_id,
                node.product_id,
                node.node_type,
                node.interface_number,
                node.descriptor_sha256,
            )
            for node in nodes
        ),
        key=repr,
    )
    return _fingerprint(identities)


def calculate_instance_fingerprint(nodes: Sequence[DeviceNode]) -> str | None:
    """Fingerprint an exact device only from a true device-unique identifier.

    USB/sysfs parent paths identify a *connection location*, not a mouse.  A
    device moved to another port must therefore keep the same model profile
    instead of being treated as a new physical instance.
    """

    unique_values = sorted({node.uniq for node in nodes if node.uniq})
    if unique_values:
        return _fingerprint(("uniq", unique_values))
    return None


def group_physical_devices(nodes: Iterable[DeviceNode]) -> list[PhysicalDevice]:
    """Group kernel interfaces into physical devices using strongest identity first."""

    grouped: dict[str, list[DeviceNode]] = {}
    for node in nodes:
        key = node.parent_key or f"unresolved:{node.bus}:{node.vendor_id}:{node.product_id}:{node.phys}"
        grouped.setdefault(key, []).append(node)

    devices: list[PhysicalDevice] = []
    for group_nodes in grouped.values():
        evdev = [node for node in group_nodes if node.node_type == "evdev"]
        hidraw = [node for node in group_nodes if node.node_type == "hidraw"]
        anchor = (evdev or hidraw)[0]
        parent = find_parent_device(anchor.sysfs_path) if anchor.sysfs_path else None
        devices.append(
            PhysicalDevice(
                name=next((node.name for node in evdev + hidraw if node.name), "Unknown mouse"),
                vendor_id=anchor.vendor_id,
                product_id=anchor.product_id,
                bus=anchor.bus,
                parent_path=parent,
                evdev_nodes=evdev,
                hidraw_nodes=hidraw,
                model_fingerprint=calculate_model_fingerprint(group_nodes),
                instance_fingerprint=calculate_instance_fingerprint(group_nodes),
            )
        )
    return devices


def _same_path(left: Path | str, right: Path | str) -> bool:
    try:
        return os.path.realpath(os.fspath(left)) == os.path.realpath(os.fspath(right))
    except OSError:
        return os.fspath(left) == os.fspath(right)


def build_device_graph(
    mouse: MouseDevice,
    *,
    evdev_nodes: Sequence[DeviceNode] | None = None,
    hidraw_nodes: Sequence[DeviceNode] | None = None,
) -> PhysicalDevice:
    """Resolve a selected evdev mouse to one physical composite HID device.

    Exact selected-node membership wins.  If that cannot be established, a
    unique matching VID/PID/bus/phys group may be used for read-only discovery.
    Multiple matches are explicitly marked ambiguous; callers must refuse
    writable operations.
    """

    evdev = list(evdev_nodes) if evdev_nodes is not None else enumerate_evdev_nodes()
    hidraw = list(hidraw_nodes) if hidraw_nodes is not None else enumerate_hidraw_nodes()
    groups = group_physical_devices([*evdev, *hidraw])

    exact = [
        group
        for group in groups
        if any(_same_path(node.path, mouse.path) for node in group.evdev_nodes)
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        candidates = exact
    else:
        candidates = []
        for group in groups:
            if mouse.vendor is not None and group.vendor_id != mouse.vendor:
                continue
            if mouse.product is not None and group.product_id != mouse.product:
                continue
            if mouse.bustype is not None and group.bus != mouse.bustype:
                continue
            if mouse.phys and not any(
                _normalized_phys(node.phys) == _normalized_phys(mouse.phys)
                for node in group.all_nodes
                if node.phys
            ):
                continue
            candidates.append(group)

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise TopologyError(f"could not correlate {mouse.path!r} with a physical HID device")

    # Preserve all evidence for diagnostics, but make ambiguity impossible to
    # overlook downstream.
    combined_nodes = [node for group in candidates for node in group.all_nodes]
    physical = PhysicalDevice(
        name=mouse.name,
        vendor_id=mouse.vendor,
        product_id=mouse.product,
        bus=mouse.bustype,
        parent_path=None,
        evdev_nodes=[node for node in combined_nodes if node.node_type == "evdev"],
        hidraw_nodes=[node for node in combined_nodes if node.node_type == "hidraw"],
        model_fingerprint=calculate_model_fingerprint(combined_nodes),
        instance_fingerprint=None,
        ambiguous=True,
    )
    physical.evidence.append(
        DiscoveryEvidence(
            EvidenceLevel.VALIDATED,
            "ambiguous-physical-device",
            f"{len(candidates)} physical devices match the selected identity; writes are forbidden",
            source="device_topology",
        )
    )
    return physical
