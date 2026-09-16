"""Known protocol-family repertoire and conservative structural matcher.

The repertoire is intentionally *not* a device support table.  It records
reusable grammar facts learned from maintained open-source implementations and
hardware verification.  Structural matches help discovery decide what to
observe next; they never grant write permission on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .discovery_models import DeviceNode, PhysicalDevice
from .hid_descriptor import ParsedHidDescriptor
from .protocol_grammar import (
    CodecKind,
    CodecSpec,
    FieldBinding,
    ProtocolFamily,
    ProtocolSource,
    ReportSignature,
    SemanticBehavior,
    SourceTrust,
    TransportKind,
    WriteScope,
)


@dataclass(frozen=True)
class FamilyCandidate:
    family: ProtocolFamily
    score: int
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    exact_identity: bool

    @property
    def write_authorized(self) -> bool:
        # A structural match is not a proven family handshake.  Backends and
        # later active validation may promote that separately.
        return self.family.can_authorize_write(exact_model=self.exact_identity)


@dataclass(frozen=True)
class ObservedReport:
    node: DeviceNode
    report_type: str
    report_id: int
    byte_length: int
    vendor_usage: bool


def _source(
    project: str,
    reference: str,
    trust: SourceTrust,
    *,
    verified_on: str | None = None,
    verified_date: str | None = None,
    notes: str = "",
) -> ProtocolSource:
    return ProtocolSource(
        project=project,
        reference=reference,
        trust=trust,
        verified_on=verified_on,
        verified_date=verified_date,
        notes=notes,
    )


# The initial repertoire deliberately records only facts we can explain and
# source.  Families with older or incomplete write verification are useful for
# classification but remain write-disabled until mouse-control proves them on
# hardware or a modern upstream verification justifies promotion.
DEFAULT_REPERTOIRE: tuple[ProtocolFamily, ...] = (
    ProtocolFamily(
        name="hidpp2",
        revision="dynamic-root",
        sources=(
            _source(
                "mouse-control",
                "G305 physical discovery acceptance",
                SourceTrust.LOCAL_PROVEN,
                verified_on="Logitech G305 LIGHTSPEED",
                verified_date="2026-09-15",
                notes="Dynamic ROOT discovery proved DPI/report-rate/battery semantics on hardware.",
            ),
        ),
        vendor_ids=(0x046D,),
        transports=(TransportKind.BACKEND, TransportKind.HID_INPUT),
        write_scope=WriteScope.BACKEND_ONLY,
        minimum_match_score=999,
        notes="Known HID++ remains a teacher/execution backend; VID alone is never a generic match.",
    ),
    ProtocolFamily(
        name="razer-rpc90",
        revision="classic-90-byte",
        sources=(
            _source(
                "OpenRazer",
                "driver/razercommon.h",
                SourceTrust.MAINTAINED,
                notes="90-byte status/transaction/class/command/payload/XOR envelope.",
            ),
            _source(
                "OpenRazer",
                "PR #2886 — Viper V3 Pro SE",
                SourceTrust.HARDWARE_VERIFIED,
                verified_on="Razer Viper V3 Pro SE wired + wireless",
                verified_date="2026-08",
                notes="Physical hardware confirmed DPI, poll rate, battery and hot-plug behavior.",
            ),
        ),
        vendor_ids=(0x1532,),
        transports=(TransportKind.USB_CONTROL, TransportKind.BACKEND),
        write_scope=WriteScope.BACKEND_ONLY,
        minimum_match_score=999,
        notes="Command-specific transaction IDs/quirks make backend execution safer than family inference.",
    ),
    ProtocolFamily(
        name="asus-rog-command64",
        revision="omni-v2",
        sources=(
            _source(
                "libratbag",
                "commit d93a8bc47496129f11b544aa5ea46f9d269c6bab",
                SourceTrust.HARDWARE_VERIFIED,
                verified_on="ASUS ROG Strix Impact III Wireless via Omni receiver",
                verified_date="2026-08-18",
                notes="Identification, DPI, report rate and settings writes round-tripped and survived restart.",
            ),
        ),
        vendor_ids=(0x0B05,),
        transports=(TransportKind.HID_OUTPUT, TransportKind.HID_INPUT),
        write_scope=WriteScope.EXACT_MODEL,
        minimum_match_score=999,
        notes="Shared receiver identity must be resolved by protocol signature, not USB PID alone.",
    ),
    ProtocolFamily(
        name="steelseries-direct-command",
        revision="multi-generation",
        sources=(
            _source(
                "rivalcfg",
                "rivalcfg/devices/aerox5.py",
                SourceTrust.MAINTAINED,
                notes="Declarative OUTPUT-report commands for DPI, polling, buttons and save.",
            ),
            _source(
                "rivalcfg",
                "PR #288 — Rival 650 lift-off distance",
                SourceTrust.HARDWARE_VERIFIED,
                verified_on="SteelSeries Rival 650 wired + wireless dongle",
                verified_date="2026-08",
                notes="Positions verified physically; persistent setting survived power cycle.",
            ),
        ),
        vendor_ids=(0x1038,),
        transports=(TransportKind.HID_OUTPUT, TransportKind.HID_INPUT),
        write_scope=WriteScope.EXACT_MODEL,
        minimum_match_score=999,
        notes="SteelSeries has multiple protocol generations; revision must be resolved before writes.",
    ),
    ProtocolFamily(
        name="sinowealth-config-blob",
        revision="classic",
        sources=(
            _source(
                "libratbag",
                "src/driver-sinowealth.c",
                SourceTrust.MAINTAINED,
                notes="Shared ODM configuration-blob protocol across Glorious/G-Wolves and sensor variants.",
            ),
        ),
        signatures=(
            ReportSignature("feature", 0x04, exact_length=520, vendor_usage_required=True, weight=7),
            ReportSignature("feature", 0x05, exact_length=6, vendor_usage_required=True, weight=7),
        ),
        transports=(TransportKind.HID_FEATURE_GET, TransportKind.HID_FEATURE_SET),
        bindings=(
            FieldBinding(
                SemanticBehavior.REPORT_RATE_HZ,
                "configuration",
                10,
                codec=CodecSpec(
                    CodecKind.ENUM,
                    values={0x01: 125, 0x02: 250, 0x03: 500, 0x04: 1000},
                ),
                evidence_note="Offset varies by layout; binding is descriptive, not write-authorizing.",
            ),
        ),
        write_scope=WriteScope.NEVER,
        minimum_match_score=14,
        notes="Strong report-shape fingerprint; writes stay disabled until modern hardware verification is imported.",
    ),
    ProtocolFamily(
        name="attackshark-x11-feature",
        revision="x11",
        sources=(
            _source(
                "OpenSharkX11",
                "docs/protocol/PROTOCOL_EN.md",
                SourceTrust.HARDWARE_VERIFIED,
                verified_on="Attack Shark X11 wired + 2.4 GHz",
                verified_date="2026",
                notes="DPI, polling, events, prerequisites and dangerous report 0x0b documented from hardware tests.",
            ),
            _source(
                "OpenMouse mouse-protocol",
                "commit eba2e832be60b361cdfbc95d7d51ab47b9522e97",
                SourceTrust.MAINTAINED,
                verified_date="2026-09-14",
                notes="Native X11 polling, DPI and battery integration merged into the maintained protocol package.",
            ),
        ),
        signatures=(
            ReportSignature("feature", 0x04, minimum_length=52, maximum_length=56, weight=6),
            ReportSignature("feature", 0x05, exact_length=15, weight=5),
            ReportSignature("feature", 0x06, exact_length=9, weight=6),
        ),
        vendor_ids=(0x1D57,),
        product_ids=(0xFA55, 0xFA60),
        transports=(TransportKind.HID_FEATURE_SET, TransportKind.USB_INTERRUPT),
        bindings=(
            FieldBinding(
                SemanticBehavior.DPI_STAGE_INDEX,
                "dpi_config",
                24,
                codec=CodecSpec(CodecKind.LINEAR, scale=1, offset=-1),
            ),
            FieldBinding(
                SemanticBehavior.REPORT_RATE_HZ,
                "polling",
                3,
                codec=CodecSpec(
                    CodecKind.ENUM,
                    values={0x08: 125, 0x04: 250, 0x02: 500, 0x01: 1000},
                ),
            ),
            FieldBinding(
                SemanticBehavior.BATTERY_PERCENT,
                "event_battery",
                4,
                codec=CodecSpec(CodecKind.U8),
            ),
        ),
        write_scope=WriteScope.EXACT_MODEL,
        minimum_match_score=12,
        notes="Report 0x0b is known dangerous and must never be generated by discovery.",
    ),
    ProtocolFamily(
        name="ajazz-aj-feature64",
        revision="aj-series",
        sources=(
            _source(
                "ajazz-control-center",
                "docs/protocols/mouse/aj_series.md",
                SourceTrust.HARDWARE_VERIFIED,
                verified_on="AJAZZ 2.4G/8K AJ-series hardware",
                verified_date="2026-05-22",
                notes="Shipping-firmware battery flow verified: 0xF7 status poll + delayed GET_FEATURE.",
            ),
        ),
        signatures=(
            ReportSignature(
                "feature",
                0x05,
                exact_length=64,
                vendor_usage_required=True,
                weight=8,
            ),
        ),
        transports=(TransportKind.HID_FEATURE_GET, TransportKind.HID_FEATURE_SET),
        write_scope=WriteScope.EXACT_MODEL,
        minimum_match_score=8,
        notes="64-byte command/subcommand/length/SUM8 envelope; battery uses a separate heartbeat transaction.",
    ),
    ProtocolFamily(
        name="mchose-v3-block-rpc",
        revision="a7-v3",
        sources=(
            _source(
                "OpenMouse mouse-protocol",
                "commit 5b0b2a93719158182176253da4c3574d47981212",
                SourceTrust.MAINTAINED,
                verified_date="2026-09-12",
                notes="Read-modify-write block grammar documented, but commit explicitly states writes were not sent to hardware.",
            ),
            _source(
                "OpenMouse mouse-protocol",
                "commit a92e865687479f2578e57e0b6d852c06570abd82",
                SourceTrust.MAINTAINED,
                verified_date="2026-09-12",
                notes="Fresh cable/receiver captures established delayed replies, refusal replies and retry semantics.",
            ),
        ),
        transports=(TransportKind.HID_FEATURE_GET, TransportKind.HID_FEATURE_SET),
        write_scope=WriteScope.NEVER,
        minimum_match_score=999,
        notes="Useful transaction-state-machine teacher; writes intentionally remain untrusted.",
    ),
)


def observed_reports(
    descriptors: Mapping[DeviceNode, ParsedHidDescriptor],
) -> tuple[ObservedReport, ...]:
    result: list[ObservedReport] = []
    for node, descriptor in descriptors.items():
        for report in descriptor.reports:
            result.append(
                ObservedReport(
                    node=node,
                    report_type=report.report_type,
                    report_id=report.report_id,
                    byte_length=report.byte_length,
                    vendor_usage=any(0xFF00 <= page <= 0xFFFF for page in report.usage_pages),
                )
            )
    return tuple(result)


def _signature_matches(signature: ReportSignature, report: ObservedReport) -> bool:
    if signature.report_type != report.report_type:
        return False
    if signature.report_id is not None and signature.report_id != report.report_id:
        return False
    if signature.exact_length is not None and signature.exact_length != report.byte_length:
        return False
    if signature.minimum_length is not None and report.byte_length < signature.minimum_length:
        return False
    if signature.maximum_length is not None and report.byte_length > signature.maximum_length:
        return False
    if (
        signature.vendor_usage_required is not None
        and signature.vendor_usage_required != report.vendor_usage
    ):
        return False
    return True


def match_repertoire(
    physical: PhysicalDevice,
    descriptors: Mapping[DeviceNode, ParsedHidDescriptor],
    *,
    families: Iterable[ProtocolFamily] = DEFAULT_REPERTOIRE,
) -> tuple[FamilyCandidate, ...]:
    """Return structural protocol-family candidates ordered by evidence score."""

    reports = observed_reports(descriptors)
    candidates: list[FamilyCandidate] = []
    for family in families:
        score = 0
        matched: list[str] = []
        missing: list[str] = []
        rejected = False

        for signature in family.signatures:
            found = any(_signature_matches(signature, report) for report in reports)
            text = (
                f"{signature.report_type}:id="
                f"{('*' if signature.report_id is None else hex(signature.report_id))}"
            )
            if found:
                score += signature.weight
                matched.append(text)
            elif signature.required:
                missing.append(text)
                rejected = True
            else:
                missing.append(text)

        vendor_match = bool(
            family.vendor_ids
            and physical.vendor_id is not None
            and physical.vendor_id in family.vendor_ids
        )
        product_match = bool(
            family.product_ids
            and physical.product_id is not None
            and physical.product_id in family.product_ids
        )
        if vendor_match:
            score += 1
            matched.append("vendor-hint")
        if product_match:
            score += 2
            matched.append("product-hint")

        # Identity hints alone are intentionally insufficient for generic
        # repertoire matching.  Families without structural signatures are
        # teachers/backends until a stronger grammar signature is added.
        if rejected or score < family.minimum_match_score:
            continue

        exact_identity = product_match and (vendor_match or not family.vendor_ids)
        candidates.append(
            FamilyCandidate(
                family=family,
                score=score,
                matched=tuple(matched),
                missing=tuple(missing),
                exact_identity=exact_identity,
            )
        )

    return tuple(sorted(candidates, key=lambda item: (-item.score, item.family.name)))
