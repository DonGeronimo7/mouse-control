"""Minimal, dependency-free HID report descriptor parser used by discovery.

The parser intentionally answers structural questions only: which report IDs
exist, whether they are Input/Output/Feature reports, their byte lengths, and
which usage pages appear in them.  Vendor-defined fields are *not* assigned
semantics such as DPI or polling rate here.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Iterable

from .discovery_models import HidReportDefinition


class HidDescriptorError(ValueError):
    """The HID report descriptor is malformed or truncated."""


@dataclass(frozen=True)
class ParsedHidDescriptor:
    raw: bytes
    reports: tuple[HidReportDefinition, ...]

    @property
    def input_reports(self) -> tuple[HidReportDefinition, ...]:
        return get_input_reports(self)

    @property
    def output_reports(self) -> tuple[HidReportDefinition, ...]:
        return get_output_reports(self)

    @property
    def feature_reports(self) -> tuple[HidReportDefinition, ...]:
        return get_feature_reports(self)


@dataclass
class _GlobalState:
    usage_page: int = 0
    report_size: int = 0
    report_count: int = 0
    report_id: int = 0

    def copy(self) -> "_GlobalState":
        return _GlobalState(
            usage_page=self.usage_page,
            report_size=self.report_size,
            report_count=self.report_count,
            report_id=self.report_id,
        )


def _unsigned(payload: bytes) -> int:
    return int.from_bytes(payload, "little", signed=False) if payload else 0


def _usage_page_from_usage(value: int, fallback: int, payload_size: int) -> int:
    if payload_size == 4 and value > 0xFFFF:
        return (value >> 16) & 0xFFFF
    return fallback


def parse_report_descriptor(raw: bytes) -> ParsedHidDescriptor:
    """Parse enough of a HID descriptor to safely inspect report structure."""

    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("raw HID descriptor must be bytes-like")
    data = bytes(raw)
    state = _GlobalState()
    stack: list[_GlobalState] = []
    local_usage_pages: set[int] = set()
    bit_lengths: dict[tuple[str, int], int] = {}
    usage_pages: dict[tuple[str, int], set[int]] = {}

    offset = 0
    while offset < len(data):
        prefix = data[offset]
        offset += 1

        if prefix == 0xFE:  # Long item: size, long tag, payload.
            if offset + 2 > len(data):
                raise HidDescriptorError("truncated HID long-item header")
            size = data[offset]
            offset += 2  # Skip size and long tag.
            if offset + size > len(data):
                raise HidDescriptorError("truncated HID long item")
            offset += size
            continue

        size_code = prefix & 0x03
        size = (0, 1, 2, 4)[size_code]
        item_type = (prefix >> 2) & 0x03
        tag = (prefix >> 4) & 0x0F
        if offset + size > len(data):
            raise HidDescriptorError("truncated HID short item")
        payload = data[offset:offset + size]
        offset += size
        value = _unsigned(payload)

        if item_type == 1:  # Global
            if tag == 0x0:  # Usage Page
                state.usage_page = value
            elif tag == 0x7:  # Report Size
                state.report_size = value
            elif tag == 0x8:  # Report ID
                if value == 0 or value > 0xFF:
                    raise HidDescriptorError(f"invalid report ID {value}")
                state.report_id = value
            elif tag == 0x9:  # Report Count
                state.report_count = value
            elif tag == 0xA:  # Push
                stack.append(state.copy())
            elif tag == 0xB:  # Pop
                if not stack:
                    raise HidDescriptorError("HID global-state pop without push")
                state = stack.pop()
            continue

        if item_type == 2:  # Local
            if tag in (0x0, 0x1, 0x2):  # Usage / Usage Min / Usage Max
                local_usage_pages.add(
                    _usage_page_from_usage(value, state.usage_page, size)
                )
            continue

        if item_type != 0:  # Reserved
            continue

        report_type = {0x8: "input", 0x9: "output", 0xB: "feature"}.get(tag)
        if report_type is not None:
            if state.report_size < 0 or state.report_count < 0:
                raise HidDescriptorError("negative report size/count")
            key = (report_type, state.report_id)
            bit_lengths[key] = bit_lengths.get(key, 0) + (
                state.report_size * state.report_count
            )
            pages = usage_pages.setdefault(key, set())
            pages.add(state.usage_page)
            pages.update(local_usage_pages)

        # Local items are reset after every Main item, including Collection.
        local_usage_pages.clear()

    reports: list[HidReportDefinition] = []
    order = {"input": 0, "output": 1, "feature": 2}
    for (report_type, report_id), bits in sorted(
        bit_lengths.items(), key=lambda item: (order[item[0][0]], item[0][1])
    ):
        payload_bytes = ceil(bits / 8) if bits else 0
        byte_length = payload_bytes + (1 if report_id else 0)
        reports.append(
            HidReportDefinition(
                report_id=report_id,
                report_type=report_type,
                byte_length=byte_length,
                usage_pages=tuple(sorted(usage_pages[(report_type, report_id)])),
            )
        )

    return ParsedHidDescriptor(raw=data, reports=tuple(reports))


def enumerate_report_ids(descriptor: ParsedHidDescriptor) -> tuple[int, ...]:
    return tuple(sorted({report.report_id for report in descriptor.reports}))


def _reports_of_type(
    descriptor: ParsedHidDescriptor, report_type: str
) -> tuple[HidReportDefinition, ...]:
    return tuple(report for report in descriptor.reports if report.report_type == report_type)


def get_input_reports(descriptor: ParsedHidDescriptor) -> tuple[HidReportDefinition, ...]:
    return _reports_of_type(descriptor, "input")


def get_output_reports(descriptor: ParsedHidDescriptor) -> tuple[HidReportDefinition, ...]:
    return _reports_of_type(descriptor, "output")


def get_feature_reports(descriptor: ParsedHidDescriptor) -> tuple[HidReportDefinition, ...]:
    return _reports_of_type(descriptor, "feature")


def calculate_report_lengths(
    descriptor: ParsedHidDescriptor,
    *,
    report_type: str | None = None,
) -> dict[int, int]:
    """Return report ID -> byte length, optionally restricted by report type.

    If the same report ID occurs in multiple report types, the maximum length
    is returned.  Callers issuing a GET_FEATURE should normally pass
    ``report_type="feature"``.
    """

    result: dict[int, int] = {}
    for report in descriptor.reports:
        if report_type is not None and report.report_type != report_type:
            continue
        result[report.report_id] = max(result.get(report.report_id, 0), report.byte_length)
    return result


def vendor_defined_reports(
    descriptor: ParsedHidDescriptor,
) -> tuple[HidReportDefinition, ...]:
    """Return reports touching HID vendor-defined usage pages (0xFF00-0xFFFF)."""

    return tuple(
        report
        for report in descriptor.reports
        if any(0xFF00 <= page <= 0xFFFF for page in report.usage_pages)
    )
