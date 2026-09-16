"""Protocol-neutral models for automatic mouse hardware discovery.

Discovery deliberately separates *observation* from *runtime control*.  These
models contain facts and evidence only; they do not open devices or write to
hardware.  Unknown hardware may be observed and correlated, but writable
support must be backed by ``EvidenceLevel.PROVEN`` evidence from a validated
protocol implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Mapping


class DiscoveryPhase(Enum):
    """Ordered high-level phases used by :class:`DiscoveryEngine`."""

    ENUMERATE = auto()
    CORRELATE = auto()
    DESCRIPTORS = auto()
    PROTOCOL = auto()
    OBSERVE = auto()
    CORRELATE_EVENTS = auto()
    VALIDATE = auto()
    COMPLETE = auto()


class EvidenceLevel(Enum):
    """Strength of a discovery claim.

    ``OBSERVED`` and ``CORRELATED`` facts are useful for learning an unknown
    protocol, but are never sufficient to authorize writes. ``VALIDATED``
    means a behavior is reproducible. ``PROVEN`` is reserved for semantics
    established by a validated protocol implementation and, where supported,
    hardware readback.
    """

    OBSERVED = auto()
    CORRELATED = auto()
    VALIDATED = auto()
    PROVEN = auto()


@dataclass(frozen=True)
class DiscoveryEvidence:
    """One auditable reason for a discovery claim."""

    level: EvidenceLevel
    code: str
    message: str
    source: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceNode:
    """One kernel node that belongs to a candidate physical device."""

    path: Path
    sysfs_path: Path | None
    subsystem: str
    node_type: str
    bus: int | None
    vendor_id: int | None
    product_id: int | None
    interface_number: int | None
    name: str = ""
    phys: str = ""
    uniq: str = ""
    descriptor_sha256: str | None = None
    parent_key: str | None = None

    @property
    def stable_interface_key(self) -> tuple[object, ...]:
        """Return interface facts that survive ``hidrawN`` renumbering."""

        return (
            self.bus,
            self.vendor_id,
            self.product_id,
            self.interface_number,
            self.descriptor_sha256,
            self.phys or None,
            self.uniq or None,
        )


@dataclass
class PhysicalDevice:
    """A physical mouse assembled from its evdev and hidraw interfaces."""

    name: str
    vendor_id: int | None
    product_id: int | None
    bus: int | None
    parent_path: Path | None
    evdev_nodes: list[DeviceNode] = field(default_factory=list)
    hidraw_nodes: list[DeviceNode] = field(default_factory=list)
    model_fingerprint: str = ""
    instance_fingerprint: str | None = None
    ambiguous: bool = False
    evidence: list[DiscoveryEvidence] = field(default_factory=list)

    @property
    def all_nodes(self) -> tuple[DeviceNode, ...]:
        return tuple(self.evdev_nodes) + tuple(self.hidraw_nodes)


@dataclass(frozen=True)
class HidReportDefinition:
    """Descriptor-derived layout for one HID report."""

    report_id: int
    report_type: str
    byte_length: int
    usage_pages: tuple[int, ...] = ()


@dataclass
class ProtocolMatch:
    """Result from a protocol detector.

    ``metadata`` must contain discovery-time facts only.  It must not contain
    an open file descriptor, live session, or device path that would become a
    persistent hardware identity.
    """

    name: str
    version: str | None
    responder: DeviceNode | None
    evidence: list[DiscoveryEvidence] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveredCapability:
    """Protocol-neutral capability discovered for a physical device."""

    name: str
    readable: bool = False
    writable: bool = False
    values: tuple[int, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None
    step: int | None = None
    evidence: list[DiscoveryEvidence] = field(default_factory=list)

    @property
    def proven(self) -> bool:
        return any(item.level is EvidenceLevel.PROVEN for item in self.evidence)

    def normalized(self) -> "DiscoveredCapability":
        """Return a safety-normalized copy.

        A write claim without PROVEN evidence is always demoted to read-only.
        This gives the safety rule a second enforcement point even if a future
        detector accidentally reports ``writable=True`` too early.
        """

        writable = self.writable and self.proven
        values = tuple(sorted(set(self.values))) if self.values is not None else None
        return DiscoveredCapability(
            name=self.name,
            readable=self.readable,
            writable=writable,
            values=values,
            minimum=self.minimum,
            maximum=self.maximum,
            step=self.step,
            evidence=list(self.evidence),
        )


@dataclass
class DiscoveryResult:
    """Complete discovery result suitable for display or profile generation."""

    device: PhysicalDevice
    protocol: ProtocolMatch | None
    capabilities: dict[str, DiscoveredCapability]
    observations: list[DiscoveryEvidence] = field(default_factory=list)
    phases: list[DiscoveryPhase] = field(default_factory=list)

    @property
    def writable(self) -> bool:
        return not self.device.ambiguous and any(
            capability.writable for capability in self.capabilities.values()
        )

    @property
    def persistent_identity(self) -> Mapping[str, object]:
        """Stable identity facts; intentionally excludes volatile device paths."""

        return {
            "bus": self.device.bus,
            "vendor_id": self.device.vendor_id,
            "product_id": self.device.product_id,
            "model_fingerprint": self.device.model_fingerprint,
            "instance_fingerprint": self.device.instance_fingerprint,
        }
