"""Human and machine-readable output for automatic hardware discovery.

Presentation is deliberately separate from discovery so the engine stays
usable by setup, tests, support tooling, and future graphical frontends.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .discovery_models import DiscoveryEvidence, DiscoveryResult


def _safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.name.lower()
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _evidence(item: DiscoveryEvidence) -> dict[str, Any]:
    return {
        "level": item.level.name.lower(),
        "code": item.code,
        "message": item.message,
        "source": item.source,
        "details": _safe(item.details),
    }


def result_to_dict(
    result: DiscoveryResult,
    *,
    profile_path: Path | None = None,
) -> dict[str, Any]:
    """Return a diagnostic dictionary without treating live node paths as identity."""

    protocol = None
    if result.protocol is not None:
        metadata = {
            key: value
            for key, value in result.protocol.metadata.items()
            if key != "capabilities"
        }
        responder = result.protocol.responder
        protocol = {
            "name": result.protocol.name,
            "version": result.protocol.version,
            "responder": None if responder is None else {
                "live_path": str(responder.path),
                "interface_number": responder.interface_number,
                "descriptor_sha256": responder.descriptor_sha256,
            },
            "metadata": _safe(metadata),
            "evidence": [_evidence(item) for item in result.protocol.evidence],
        }

    capabilities: dict[str, Any] = {}
    for name, raw in sorted(result.capabilities.items()):
        capability = raw.normalized()
        capabilities[name] = {
            "readable": capability.readable,
            "writable": capability.writable,
            "values": list(capability.values) if capability.values is not None else None,
            "minimum": capability.minimum,
            "maximum": capability.maximum,
            "step": capability.step,
            "evidence": [_evidence(item) for item in capability.evidence],
        }

    return {
        "device": {
            "name": result.device.name,
            "vendor_id": result.device.vendor_id,
            "product_id": result.device.product_id,
            "bus": result.device.bus,
            "ambiguous": result.device.ambiguous,
            "evdev_interfaces": len(result.device.evdev_nodes),
            "hidraw_interfaces": len(result.device.hidraw_nodes),
            "model_fingerprint": result.device.model_fingerprint,
            "instance_fingerprint": result.device.instance_fingerprint,
        },
        "protocol": protocol,
        "capabilities": capabilities,
        "phases": [phase.name.lower() for phase in result.phases],
        "profile_path": str(profile_path) if profile_path is not None else None,
        "observations": [_evidence(item) for item in result.observations],
    }


def _identity(result: DiscoveryResult) -> str:
    vendor = result.device.vendor_id
    product = result.device.product_id
    if isinstance(vendor, int) and isinstance(product, int):
        return f"{vendor:04x}:{product:04x}"
    return "unknown"


def _capability_line(name: str, result: DiscoveryResult) -> str:
    capability = result.capabilities[name].normalized()
    access = "read/write" if capability.writable else "read-only" if capability.readable else "detected"
    details: list[str] = []
    if capability.values:
        suffix = " Hz" if name == "report_rate" else ""
        details.append(", ".join(f"{value}{suffix}" for value in capability.values))
    if capability.minimum is not None and capability.maximum is not None:
        text = f"{capability.minimum}–{capability.maximum}"
        if capability.step is not None:
            text += f" (step {capability.step})"
        details.append(text)
    rendered_name = name.replace("_", " ").title()
    return f"  {rendered_name}: {access}" + (f" — {'; '.join(details)}" if details else "")


def render_discovery_result(
    result: DiscoveryResult,
    *,
    profile_path: Path | None = None,
    verbose: bool = False,
) -> str:
    """Render one discovery result for a terminal hardware test."""

    lines = [
        "Mouse Control Automatic Hardware Discovery",
        "=" * 42,
        f"Device: {result.device.name} [{_identity(result)}]",
        f"Interfaces: {len(result.device.evdev_nodes)} evdev, {len(result.device.hidraw_nodes)} hidraw",
        f"Physical binding: {'AMBIGUOUS — WRITES FORBIDDEN' if result.device.ambiguous else 'unambiguous'}",
    ]

    if result.protocol is None:
        lines.extend([
            "Protocol: unknown / not yet validated",
            "Safety: unknown HID was inspected read-only; no generic write semantics were invented.",
        ])
    else:
        version = f" {result.protocol.version}" if result.protocol.version else ""
        lines.append(f"Protocol: {result.protocol.name}{version}")
        if result.protocol.responder is not None:
            node = result.protocol.responder
            iface = "?" if node.interface_number is None else str(node.interface_number)
            lines.append(f"Live responder: {node.path} (interface {iface})")

    lines.append("Capabilities:")
    if result.capabilities:
        lines.extend(_capability_line(name, result) for name in sorted(result.capabilities))
    else:
        lines.append("  No vendor capability semantics proven yet.")

    if profile_path is not None:
        lines.append(f"Profile: {profile_path}")
    elif not result.device.ambiguous:
        lines.append("Profile: not saved")

    lines.append(
        "Validated write semantics: "
        + ("available through known runtime policy" if result.writable else "none")
    )

    if verbose:
        lines.append("Phases: " + " -> ".join(phase.name.lower() for phase in result.phases))
        evidence = [
            *(result.protocol.evidence if result.protocol is not None else []),
            *result.observations,
        ]
        for capability in result.capabilities.values():
            evidence.extend(capability.evidence)
        lines.append("Evidence:")
        if evidence:
            for item in evidence:
                source = f" [{item.source}]" if item.source else ""
                lines.append(
                    f"  {item.level.name.lower():10s} {item.code}{source}: {item.message}"
                )
        else:
            lines.append("  none")

    return "\n".join(lines)
