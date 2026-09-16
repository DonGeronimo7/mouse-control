"""Tests for the vendor-agnostic protocol repertoire and inference core."""

from __future__ import annotations

from pathlib import Path

from mouse_control.discovery_models import DeviceNode, HidReportDefinition, PhysicalDevice
from mouse_control.event_correlation import CorrelationCandidate
from mouse_control.hid_descriptor import ParsedHidDescriptor
from mouse_control.protocol_codec import (
    decode_value,
    encode_value,
    fit_linear_codec,
    infer_polling_codecs,
    infer_trailing_checksums,
)
from mouse_control.protocol_grammar import CodecKind, CodecSpec
from mouse_control.protocol_repertoire import DEFAULT_REPERTOIRE, match_repertoire
from mouse_control.semantic_inference import infer_stage_hypotheses


def node(path: str = "/dev/hidraw8", *, vendor: int = 0x1234, product: int = 0x5678):
    return DeviceNode(
        path=Path(path),
        sysfs_path=None,
        subsystem="hidraw",
        node_type="hidraw",
        bus=3,
        vendor_id=vendor,
        product_id=product,
        interface_number=2,
        parent_key="parent:test",
    )


def physical(*, vendor: int = 0x1234, product: int = 0x5678, n: DeviceNode | None = None):
    n = n or node(vendor=vendor, product=product)
    return PhysicalDevice(
        name="Test Mouse",
        vendor_id=vendor,
        product_id=product,
        bus=3,
        parent_path=None,
        hidraw_nodes=[n],
        model_fingerprint="model",
    )


def descriptor(*reports: HidReportDefinition):
    return ParsedHidDescriptor(raw=b"", reports=tuple(reports))


def family(name: str):
    return next(item for item in DEFAULT_REPERTOIRE if item.name == name)


def test_codec_roundtrips_linear_reciprocal_and_enum():
    linear = CodecSpec(CodecKind.LINEAR, scale=50, offset=50)
    assert decode_value(31, linear) == 1600
    assert encode_value(1600, linear) == b"\x1f"

    reciprocal = CodecSpec(CodecKind.RECIPROCAL, base=1000)
    assert decode_value(2, reciprocal) == 500
    assert encode_value(500, reciprocal) == b"\x02"

    enum = CodecSpec(CodecKind.ENUM, values={4: 125, 3: 250, 2: 500, 1: 1000})
    assert decode_value(1, enum) == 1000
    assert encode_value(250, enum) == b"\x03"


def test_linear_dpi_codec_is_inferred_from_exact_observations():
    spec = fit_linear_codec(((0, 50), (1, 100), (31, 1600)))
    assert spec is not None
    assert spec.kind is CodecKind.LINEAR
    assert spec.scale == 50
    assert spec.offset == 50


def test_polling_inference_recognizes_period_divisor_family():
    hypotheses = infer_polling_codecs((8, 4, 2, 1))
    assert hypotheses
    assert any(
        dict(item.mapping) == {1: 1000, 2: 500, 4: 250, 8: 125}
        for item in hypotheses
    )


def test_sum8_frame_checksum_is_inferred():
    samples = []
    for command, payload in ((0x21, (1, 2, 3)), (0x22, (4, 5)), (0x23, (6,))):
        frame = bytearray(64)
        frame[0] = 0x05
        frame[1] = command
        frame[2] = 0
        frame[3] = len(payload)
        frame[4:4 + len(payload)] = bytes(payload)
        frame[63] = sum(frame[1:63]) & 0xFF
        samples.append(bytes(frame))
    inferred = infer_trailing_checksums(samples)
    assert any(item.algorithm == "sum8" and item.start == 1 for item in inferred)


def test_sinowealth_family_can_match_cross_brand_from_report_grammar():
    n = node(vendor=0x9999, product=0x1111)
    d = descriptor(
        HidReportDefinition(0x04, "feature", 520, (0xFF00,)),
        HidReportDefinition(0x05, "feature", 6, (0xFF00,)),
    )
    matches = match_repertoire(physical(vendor=0x9999, product=0x1111, n=n), {n: d})
    assert matches
    assert matches[0].family.name == "sinowealth-config-blob"
    assert matches[0].score >= matches[0].family.minimum_match_score
    assert not matches[0].write_authorized


def test_identity_hint_alone_cannot_match_hidpp_teacher():
    n = node(vendor=0x046D, product=0x4074)
    matches = match_repertoire(physical(vendor=0x046D, product=0x4074, n=n), {n: descriptor()})
    assert all(item.family.name != "hidpp2" for item in matches)


def test_attackshark_requires_structural_signature_not_just_vid_pid():
    n = node(vendor=0x1D57, product=0xFA60)
    missing = match_repertoire(
        physical(vendor=0x1D57, product=0xFA60, n=n),
        {n: descriptor(HidReportDefinition(0x04, "feature", 56, (0xFF00,)))},
    )
    assert all(item.family.name != "attackshark-x11-feature" for item in missing)

    complete = match_repertoire(
        physical(vendor=0x1D57, product=0xFA60, n=n),
        {
            n: descriptor(
                HidReportDefinition(0x04, "feature", 56, (0xFF00,)),
                HidReportDefinition(0x05, "feature", 15, (0xFF00,)),
                HidReportDefinition(0x06, "feature", 9, (0xFF00,)),
            )
        },
    )
    attack = next(item for item in complete if item.family.name == "attackshark-x11-feature")
    assert attack.exact_identity
    # The repertoire is strong enough to describe the known exact model, but
    # DiscoveryEngine still does not manufacture writable capabilities from a
    # repertoire match. Writes require a later execution/validation layer.
    assert attack.write_authorized


def test_stage_inference_labels_repeated_small_state_changes_only_as_hypothesis():
    candidate = CorrelationCandidate(
        report_key=5,
        offset=2,
        observations=3,
        values=(1, 2, 3),
        transitions=((1, 2), (2, 3), (3, 1)),
    )
    result = infer_stage_hypotheses((candidate,))
    assert len(result) == 1
    assert result[0].behavior.value == "dpi_stage_index"
    assert result[0].confidence == "correlated"
