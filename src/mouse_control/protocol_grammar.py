"""Declarative protocol grammar used by automatic hardware discovery.

The grammar describes *how* a mouse protocol is shaped without coupling that
shape to a particular runtime backend. Known backends (HID++, OpenRazer,
etc.) can act as high-confidence teachers, while unknown devices can be
classified against the same vocabulary without receiving speculative writes.

Protocol facts are intentionally separated into three layers:

* transport/frame grammar — how bytes move and how requests are correlated;
* codecs/state models — how raw values represent DPI, report rate, stages, etc.;
* provenance/safety — how strongly a rule is verified and whether it may ever
  authorize a write.

A family resemblance is never, by itself, permission to write hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class SourceTrust(str, Enum):
    """How strongly an upstream protocol fact has been verified."""

    REFERENCE = "reference"
    MAINTAINED = "maintained"
    HARDWARE_VERIFIED = "hardware_verified"
    LOCAL_PROVEN = "local_proven"


class WriteScope(str, Enum):
    """How far a proven write rule may safely transfer."""

    NEVER = "never"
    BACKEND_ONLY = "backend_only"
    EXACT_MODEL = "exact_model"
    PROTOCOL_FAMILY = "protocol_family"


class SafetyClass(str, Enum):
    """Risk class for one transaction or command."""

    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    PERSISTENT = "persistent"
    DANGEROUS = "dangerous"
    UNKNOWN = "unknown"


class ControlOwnership(str, Enum):
    """Who currently owns behaviors normally implemented by device firmware."""

    NATIVE = "native"
    HOST = "host"


class TransportKind(str, Enum):
    HID_INPUT = "hid_input"
    HID_OUTPUT = "hid_output"
    HID_FEATURE_GET = "hid_feature_get"
    HID_FEATURE_SET = "hid_feature_set"
    USB_CONTROL = "usb_control"
    USB_INTERRUPT = "usb_interrupt"
    BLE_GATT_READ = "ble_gatt_read"
    BLE_GATT_WRITE = "ble_gatt_write"
    BLE_NOTIFY = "ble_notify"
    BACKEND = "backend"


class CodecKind(str, Enum):
    IDENTITY = "identity"
    U8 = "u8"
    U16_LE = "u16_le"
    U16_BE = "u16_be"
    ENUM = "enum"
    LINEAR = "linear"
    RECIPROCAL = "reciprocal"
    BITFIELD = "bitfield"
    LOOKUP = "lookup"


class SemanticBehavior(str, Enum):
    DPI_VALUE = "dpi_value"
    DPI_X = "dpi_x"
    DPI_Y = "dpi_y"
    DPI_STAGE_INDEX = "dpi_stage_index"
    DPI_STAGE_COUNT = "dpi_stage_count"
    DPI_STAGE_ENABLED = "dpi_stage_enabled"
    DPI_CYCLE_TRIGGER = "dpi_cycle_trigger"
    REPORT_RATE_HZ = "report_rate_hz"
    BATTERY_PERCENT = "battery_percent"
    BUTTON_BINDING = "button_binding"
    PROFILE_INDEX = "profile_index"
    LIFT_OFF_DISTANCE = "lift_off_distance"
    DEBOUNCE_MS = "debounce_ms"
    ANGLE_SNAPPING = "angle_snapping"
    MOTION_SYNC = "motion_sync"
    RIPPLE_CONTROL = "ripple_control"
    SLEEP_TIMEOUT = "sleep_timeout"


@dataclass(frozen=True)
class ProtocolSource:
    """Auditable provenance for protocol knowledge."""

    project: str
    reference: str
    trust: SourceTrust
    verified_on: str | None = None
    verified_date: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class ReportSignature:
    """One descriptor-level report pattern used for family matching."""

    report_type: str
    report_id: int | None = None
    exact_length: int | None = None
    minimum_length: int | None = None
    maximum_length: int | None = None
    vendor_usage_required: bool | None = None
    required: bool = True
    weight: int = 4

    def __post_init__(self) -> None:
        if self.report_type not in {"input", "output", "feature"}:
            raise ValueError(f"unsupported HID report type {self.report_type!r}")
        if self.report_id is not None and not 0 <= self.report_id <= 0xFF:
            raise ValueError("report_id must fit in one byte")
        if self.exact_length is not None and (
            self.minimum_length is not None or self.maximum_length is not None
        ):
            raise ValueError("exact_length cannot be combined with min/max length")
        for value in (self.exact_length, self.minimum_length, self.maximum_length):
            if value is not None and value < 0:
                raise ValueError("report lengths cannot be negative")
        if (
            self.minimum_length is not None
            and self.maximum_length is not None
            and self.minimum_length > self.maximum_length
        ):
            raise ValueError("minimum_length cannot exceed maximum_length")
        if self.weight < 0:
            raise ValueError("weight cannot be negative")


@dataclass(frozen=True)
class CodecSpec:
    """Declarative raw-value codec.

    LINEAR uses ``semantic = raw * scale + offset``.
    RECIPROCAL uses ``semantic = base / raw``.
    ENUM/LOOKUP use ``values`` as raw -> semantic mappings.
    BITFIELD extracts ``(raw >> shift) & mask`` before optional linear scaling.
    """

    kind: CodecKind
    scale: int = 1
    offset: int = 0
    base: int | None = None
    mask: int | None = None
    shift: int = 0
    values: Mapping[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind is CodecKind.RECIPROCAL and not self.base:
            raise ValueError("reciprocal codec requires a non-zero base")
        if self.kind in {CodecKind.ENUM, CodecKind.LOOKUP} and not self.values:
            raise ValueError(f"{self.kind.value} codec requires values")
        if self.kind is CodecKind.BITFIELD and self.mask is None:
            raise ValueError("bitfield codec requires a mask")
        if self.shift < 0:
            raise ValueError("shift cannot be negative")


@dataclass(frozen=True)
class FieldBinding:
    """Bind a raw field to one protocol-neutral mouse behavior."""

    behavior: SemanticBehavior
    frame: str
    offset: int
    width: int = 1
    codec: CodecSpec = field(default_factory=lambda: CodecSpec(CodecKind.IDENTITY))
    evidence_note: str = ""

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("field offset cannot be negative")
        if self.width <= 0:
            raise ValueError("field width must be positive")


@dataclass(frozen=True)
class TransactionStep:
    """One state-machine step in a protocol transaction."""

    operation: str
    frame: str | None = None
    transport: TransportKind | None = None
    delay_ms: int = 0
    retries: int = 0
    condition: str | None = None

    def __post_init__(self) -> None:
        if self.delay_ms < 0 or self.retries < 0:
            raise ValueError("transaction delay/retries cannot be negative")


@dataclass(frozen=True)
class TransactionSpec:
    name: str
    steps: tuple[TransactionStep, ...]
    safety: SafetyClass = SafetyClass.UNKNOWN
    checksum: str | None = None
    verification: tuple[str, ...] = ()
    prerequisite: tuple[str, ...] = ()
    required_control: ControlOwnership | None = None
    resulting_control: ControlOwnership | None = None


@dataclass(frozen=True)
class ProtocolFamily:
    """Reusable protocol-family knowledge.

    ``vendor_ids`` and ``product_ids`` are only hints unless a write gate says
    otherwise. The matcher is expected to prefer structural signatures over
    identity so protocol knowledge can transfer across brands/ODMs.
    """

    name: str
    revision: str
    sources: tuple[ProtocolSource, ...]
    signatures: tuple[ReportSignature, ...] = ()
    vendor_ids: tuple[int, ...] = ()
    product_ids: tuple[int, ...] = ()
    transports: tuple[TransportKind, ...] = ()
    bindings: tuple[FieldBinding, ...] = ()
    transactions: tuple[TransactionSpec, ...] = ()
    write_scope: WriteScope = WriteScope.NEVER
    minimum_match_score: int = 4
    notes: str = ""

    @property
    def strongest_trust(self) -> SourceTrust:
        order = {
            SourceTrust.REFERENCE: 0,
            SourceTrust.MAINTAINED: 1,
            SourceTrust.HARDWARE_VERIFIED: 2,
            SourceTrust.LOCAL_PROVEN: 3,
        }
        if not self.sources:
            return SourceTrust.REFERENCE
        return max((source.trust for source in self.sources), key=order.__getitem__)

    @property
    def has_hardware_verified_source(self) -> bool:
        return any(
            source.trust in {SourceTrust.HARDWARE_VERIFIED, SourceTrust.LOCAL_PROVEN}
            for source in self.sources
        )

    def can_authorize_write(
        self,
        *,
        exact_model: bool = False,
        backend_proven: bool = False,
        family_handshake_proven: bool = False,
    ) -> bool:
        """Return whether this family is strong enough to authorize writes.

        A descriptor/family match alone is deliberately insufficient. A
        backend that has already proved the semantics may write. Otherwise the
        repertoire must have hardware-verified provenance and satisfy the
        declared write scope.
        """

        if backend_proven:
            return True
        if not self.has_hardware_verified_source:
            return False
        if self.write_scope is WriteScope.NEVER:
            return False
        if self.write_scope is WriteScope.BACKEND_ONLY:
            return False
        if self.write_scope is WriteScope.EXACT_MODEL:
            return exact_model
        if self.write_scope is WriteScope.PROTOCOL_FAMILY:
            return family_handshake_proven
        return False