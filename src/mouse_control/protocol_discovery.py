"""Known-protocol detectors used by automatic hardware discovery.

Protocol detection is deliberately conservative.  A detector may only probe a
vendor/device family for which its handshake is already understood.  Generic
unknown hardware never receives speculative protocol requests from this module.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from .discovery_models import (
    DeviceNode,
    DiscoveredCapability,
    DiscoveryEvidence,
    EvidenceLevel,
    PhysicalDevice,
    ProtocolMatch,
)
from .hid_session import HidSession
from .hidpp import HidppError, LOGITECH_VENDOR_ID


class ProtocolDetectionError(RuntimeError):
    """Known protocol probing failed in a way discovery should report."""


class ProtocolAmbiguityError(ProtocolDetectionError):
    """More than one interface or device claimed the same writable protocol."""


class ProtocolDetector(Protocol):
    name: str

    def detect(self, device: PhysicalDevice) -> ProtocolMatch | None:
        ...


def _dpi_capability(driver: Any) -> DiscoveredCapability | None:
    caps = driver.capabilities.dpi
    if not (caps.readable or caps.writable or caps.values or caps.ranges):
        return None
    values = tuple(caps.values or ()) or None
    minimum = maximum = step = None
    if caps.ranges:
        # Current protocol-neutral model represents one simple range.  Preserve
        # all explicit values and only publish min/max/step when the driver has
        # exactly one range rather than flattening disjoint ranges incorrectly.
        if len(caps.ranges) == 1:
            item = caps.ranges[0]
            minimum, maximum, step = item.minimum, item.maximum, item.step
    evidence = [
        DiscoveryEvidence(
            EvidenceLevel.PROVEN,
            "hidpp-adjustable-dpi",
            "HID++ ROOT dynamically exposed a validated adjustable-DPI feature",
            source="hidpp_driver",
            details={"readback_on_write": bool(caps.writable)},
        )
    ]
    return DiscoveredCapability(
        name="dpi",
        readable=caps.readable,
        writable=caps.writable,
        values=values,
        minimum=minimum,
        maximum=maximum,
        step=step,
        evidence=evidence,
    )


def _report_rate_capability(driver: Any) -> DiscoveredCapability | None:
    caps = driver.capabilities.report_rate
    if not (caps.readable or caps.writable or caps.values):
        return None
    return DiscoveredCapability(
        name="report_rate",
        readable=caps.readable,
        writable=caps.writable,
        values=tuple(caps.values or ()) or None,
        evidence=[
            DiscoveryEvidence(
                EvidenceLevel.PROVEN,
                "hidpp-report-rate",
                "HID++ ROOT dynamically exposed the validated report-rate feature",
                source="hidpp_driver",
                details={
                    "runtime_policy_required": True,
                    "readback_on_write": bool(caps.writable),
                },
            )
        ],
    )


def _battery_capability(driver: Any) -> DiscoveredCapability | None:
    caps = driver.capabilities.battery
    if not caps.readable:
        return None
    return DiscoveredCapability(
        name="battery",
        readable=True,
        writable=False,
        evidence=[
            DiscoveryEvidence(
                EvidenceLevel.PROVEN,
                "hidpp-battery",
                "HID++ ROOT exposed a battery feature with a validated decoder",
                source="hidpp_driver",
            )
        ],
    )


def capabilities_from_hidpp(driver: Any) -> dict[str, DiscoveredCapability]:
    """Convert independent HID++ capabilities without coupling failures."""

    capabilities: dict[str, DiscoveredCapability] = {}
    for converter in (_dpi_capability, _report_rate_capability, _battery_capability):
        capability = converter(driver)
        if capability is not None:
            capabilities[capability.name] = capability.normalized()
    return capabilities


class Hidpp20Detector:
    """Detect one unambiguous Logitech HID++ 2 responder.

    The detector reuses the project's already-validated ROOT discovery.  It
    never hard-codes feature indexes, and every unsuccessful session is closed.

    The HID++ driver import is intentionally lazy.  ``hidpp_driver`` imports
    protocol-neutral types from ``mouse_control.hardware``; importing it while
    this discovery module itself is being imported would otherwise re-enter the
    hardware registry through ``hardware.__init__`` and create a package import
    cycle in installed console-script startup.
    """

    name = "hidpp2"

    def __init__(self, *, session_factory=HidSession, connector=None) -> None:
        self._session_factory = session_factory
        self._connector = connector

    def _connect(self, session):
        connector = self._connector
        if connector is None:
            # Delay this import until hardware package initialization has
            # completed.  Tests may inject a connector without importing the
            # concrete HID++ driver at all.
            from .hidpp_driver import connect_hidpp20

            connector = connect_hidpp20
        return connector(session)

    def detect(self, device: PhysicalDevice) -> ProtocolMatch | None:
        if device.ambiguous:
            raise ProtocolAmbiguityError(
                "physical device identity is ambiguous; refusing protocol binding"
            )
        if device.vendor_id != LOGITECH_VENDOR_ID:
            return None

        matches: list[tuple[DeviceNode, str, dict[str, DiscoveredCapability], dict[str, Any]]] = []
        errors: list[str] = []
        for interface in device.hidraw_nodes:
            session = None
            try:
                session = self._session_factory(interface.path)
                driver = self._connect(session)
                raw_version = driver.protocol_version
                version = (
                    ".".join(str(part) for part in raw_version)
                    if isinstance(raw_version, (tuple, list))
                    else str(raw_version)
                )
                capabilities = capabilities_from_hidpp(driver)
                feature_ids = tuple(sorted(driver.features))
                matches.append(
                    (
                        interface,
                        version,
                        capabilities,
                        {
                            "device_index": driver.device_index,
                            "feature_ids": feature_ids,
                            "device_name": driver.name,
                        },
                    )
                )
            except (OSError, PermissionError, HidppError) as exc:
                errors.append(f"{interface.path.name}: {exc}")
            finally:
                if session is not None:
                    session.close()

        if not matches:
            return None
        if len(matches) != 1:
            raise ProtocolAmbiguityError(
                f"{len(matches)} hidraw interfaces claimed HID++ 2; refusing ambiguous binding"
            )

        responder, version, capabilities, metadata = matches[0]
        metadata["capabilities"] = capabilities
        return ProtocolMatch(
            name=self.name,
            version=version,
            responder=responder,
            evidence=[
                DiscoveryEvidence(
                    EvidenceLevel.PROVEN,
                    "hidpp2-single-responder",
                    "Exactly one interface completed dynamic HID++ 2 ROOT discovery",
                    source="protocol_discovery",
                    details={
                        "device_index": metadata["device_index"],
                        "failed_candidates": tuple(errors),
                    },
                )
            ],
            metadata=metadata,
        )


def detect_known_protocol(
    device: PhysicalDevice,
    detectors: Iterable[ProtocolDetector] | None = None,
) -> ProtocolMatch | None:
    """Run detectors in order and return at most one claimed protocol."""

    detector_list = tuple(detectors) if detectors is not None else (Hidpp20Detector(),)
    claims: list[ProtocolMatch] = []
    for detector in detector_list:
        match = detector.detect(device)
        if match is not None:
            claims.append(match)
    if not claims:
        return None
    if len(claims) > 1:
        names = ", ".join(match.name for match in claims)
        raise ProtocolAmbiguityError(
            f"multiple known protocol detectors claimed the device: {names}"
        )
    return claims[0]
