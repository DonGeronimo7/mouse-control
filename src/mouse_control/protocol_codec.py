"""Reusable value codecs and frame-analysis helpers for protocol discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .protocol_grammar import CodecKind, CodecSpec


STANDARD_REPORT_RATES = (125, 250, 500, 1000, 2000, 4000, 8000)


class ProtocolCodecError(ValueError):
    """A raw/semantic value cannot be represented by a codec."""


def _read_integer(data: bytes, *, width: int, little_endian: bool) -> int:
    if width <= 0:
        raise ProtocolCodecError("width must be positive")
    if len(data) < width:
        raise ProtocolCodecError(f"need {width} bytes, got {len(data)}")
    return int.from_bytes(data[:width], "little" if little_endian else "big")


def decode_value(raw: int | bytes, spec: CodecSpec, *, width: int = 1) -> int:
    """Decode one raw protocol value into its semantic integer."""

    if isinstance(raw, bytes):
        if spec.kind is CodecKind.U16_LE:
            value = _read_integer(raw, width=max(width, 2), little_endian=True)
        elif spec.kind is CodecKind.U16_BE:
            value = _read_integer(raw, width=max(width, 2), little_endian=False)
        else:
            value = _read_integer(raw, width=width, little_endian=True)
    else:
        value = int(raw)

    if spec.kind in {CodecKind.IDENTITY, CodecKind.U8, CodecKind.U16_LE, CodecKind.U16_BE}:
        return value
    if spec.kind in {CodecKind.ENUM, CodecKind.LOOKUP}:
        try:
            return int(spec.values[value])
        except KeyError as exc:
            raise ProtocolCodecError(f"raw value {value} is not in codec table") from exc
    if spec.kind is CodecKind.LINEAR:
        return value * spec.scale + spec.offset
    if spec.kind is CodecKind.RECIPROCAL:
        if value == 0:
            raise ProtocolCodecError("reciprocal codec cannot decode zero")
        assert spec.base is not None
        if spec.base % value:
            raise ProtocolCodecError(f"{spec.base} is not evenly divisible by {value}")
        return spec.base // value
    if spec.kind is CodecKind.BITFIELD:
        assert spec.mask is not None
        extracted = (value >> spec.shift) & spec.mask
        return extracted * spec.scale + spec.offset
    raise ProtocolCodecError(f"unsupported codec {spec.kind.value}")


def encode_value(value: int, spec: CodecSpec, *, width: int = 1) -> bytes:
    """Encode a semantic integer into protocol bytes."""

    semantic = int(value)
    raw: int
    if spec.kind in {CodecKind.IDENTITY, CodecKind.U8, CodecKind.U16_LE, CodecKind.U16_BE}:
        raw = semantic
    elif spec.kind in {CodecKind.ENUM, CodecKind.LOOKUP}:
        inverse = {int(mapped): int(key) for key, mapped in spec.values.items()}
        try:
            raw = inverse[semantic]
        except KeyError as exc:
            raise ProtocolCodecError(f"semantic value {semantic} is not in codec table") from exc
    elif spec.kind is CodecKind.LINEAR:
        if spec.scale == 0:
            raise ProtocolCodecError("linear codec scale cannot be zero")
        numerator = semantic - spec.offset
        if numerator % spec.scale:
            raise ProtocolCodecError(
                f"semantic value {semantic} is not aligned to scale {spec.scale}"
            )
        raw = numerator // spec.scale
    elif spec.kind is CodecKind.RECIPROCAL:
        assert spec.base is not None
        if semantic == 0 or spec.base % semantic:
            raise ProtocolCodecError(
                f"semantic value {semantic} does not divide reciprocal base {spec.base}"
            )
        raw = spec.base // semantic
    elif spec.kind is CodecKind.BITFIELD:
        assert spec.mask is not None
        if spec.scale == 0:
            raise ProtocolCodecError("bitfield scale cannot be zero")
        numerator = semantic - spec.offset
        if numerator % spec.scale:
            raise ProtocolCodecError("semantic value is not aligned to bitfield scale")
        extracted = numerator // spec.scale
        if extracted & ~spec.mask:
            raise ProtocolCodecError("semantic value exceeds bitfield mask")
        raw = extracted << spec.shift
    else:
        raise ProtocolCodecError(f"unsupported codec {spec.kind.value}")

    if raw < 0:
        raise ProtocolCodecError("encoded value cannot be negative")
    byteorder = "big" if spec.kind is CodecKind.U16_BE else "little"
    size = max(width, 2 if spec.kind in {CodecKind.U16_LE, CodecKind.U16_BE} else 1)
    try:
        return raw.to_bytes(size, byteorder)
    except OverflowError as exc:
        raise ProtocolCodecError(f"raw value {raw} does not fit in {size} bytes") from exc


def xor8(data: Iterable[int]) -> int:
    result = 0
    for value in data:
        result ^= int(value) & 0xFF
    return result


def sum8(data: Iterable[int]) -> int:
    return sum(int(value) & 0xFF for value in data) & 0xFF


def sum16(data: Iterable[int]) -> int:
    return sum(int(value) & 0xFF for value in data) & 0xFFFF


def ones_complement8(data: Iterable[int]) -> int:
    return (~sum8(data)) & 0xFF


def twos_complement8(data: Iterable[int]) -> int:
    return (-sum8(data)) & 0xFF


CHECKSUMS = {
    "xor8": xor8,
    "sum8": sum8,
    "sum16": sum16,
    "ones_complement8": ones_complement8,
    "twos_complement8": twos_complement8,
}


@dataclass(frozen=True)
class ChecksumHypothesis:
    algorithm: str
    checksum_offset: int
    start: int
    end: int
    endian: str = "little"


def infer_trailing_checksums(
    samples: Sequence[bytes],
    *,
    checksum_widths: tuple[int, ...] = (1, 2),
    start_offsets: tuple[int, ...] = (0, 1, 2, 3),
) -> tuple[ChecksumHypothesis, ...]:
    """Find simple checksum algorithms that validate every sample.

    This intentionally tests only common, explainable checksum families.  A
    match is evidence for frame structure, not permission to mutate hardware.
    """

    usable = tuple(bytes(sample) for sample in samples if sample)
    if len(usable) < 2:
        return ()
    result: list[ChecksumHypothesis] = []
    for width in checksum_widths:
        if any(len(sample) <= width for sample in usable):
            continue
        for start in start_offsets:
            if any(start >= len(sample) - width for sample in usable):
                continue
            if width == 1:
                for name in ("xor8", "sum8", "ones_complement8", "twos_complement8"):
                    fn = CHECKSUMS[name]
                    if all(fn(sample[start:-1]) == sample[-1] for sample in usable):
                        result.append(
                            ChecksumHypothesis(name, -1, start, -1, "little")
                        )
            elif width == 2:
                for endian in ("little", "big"):
                    if all(
                        sum16(sample[start:-2])
                        == int.from_bytes(sample[-2:], endian)
                        for sample in usable
                    ):
                        result.append(
                            ChecksumHypothesis("sum16", -2, start, -2, endian)
                        )
    return tuple(result)


@dataclass(frozen=True)
class ColumnRole:
    offset: int
    role: str
    values: tuple[int, ...]


def classify_columns(samples: Sequence[bytes]) -> tuple[ColumnRole, ...]:
    """Classify aligned frame bytes as constant, binary, enum, counter-like, or variable."""

    usable = tuple(bytes(sample) for sample in samples)
    if len(usable) < 2 or not usable:
        return ()
    width = min(len(sample) for sample in usable)
    roles: list[ColumnRole] = []
    for offset in range(width):
        values = tuple(sample[offset] for sample in usable)
        unique = tuple(sorted(set(values)))
        if len(unique) == 1:
            role = "constant"
        elif set(unique) <= {0, 1}:
            role = "binary"
        elif len(unique) <= min(8, len(usable)):
            role = "enum"
        else:
            deltas = [((values[index] - values[index - 1]) & 0xFF) for index in range(1, len(values))]
            role = "counter" if deltas and len(set(deltas)) == 1 else "variable"
        roles.append(ColumnRole(offset, role, unique))
    return tuple(roles)


@dataclass(frozen=True)
class PollingCodecHypothesis:
    name: str
    mapping: Mapping[int, int]


def infer_polling_codecs(raw_values: Iterable[int]) -> tuple[PollingCodecHypothesis, ...]:
    """Infer common gaming-mouse polling encodings from observed raw values."""

    values = tuple(sorted({int(value) & 0xFF for value in raw_values}))
    if len(values) < 2:
        return ()
    candidates: list[PollingCodecHypothesis] = []

    reciprocal_1000 = {
        raw: 1000 // raw
        for raw in values
        if raw and 1000 % raw == 0 and 1000 // raw in STANDARD_REPORT_RATES
    }
    if len(reciprocal_1000) == len(values):
        candidates.append(PollingCodecHypothesis("1000_divisor", reciprocal_1000))

    reciprocal_8000 = {
        raw: 8000 // raw
        for raw in values
        if raw and 8000 % raw == 0 and 8000 // raw in STANDARD_REPORT_RATES
    }
    if len(reciprocal_8000) == len(values):
        candidates.append(PollingCodecHypothesis("8000_divisor", reciprocal_8000))

    if set(values) <= {1, 2, 4, 8}:
        millisecond = {raw: 1000 // raw for raw in values}
        if all(rate in STANDARD_REPORT_RATES for rate in millisecond.values()):
            candidates.append(PollingCodecHypothesis("period_ms", millisecond))

    if values == tuple(range(values[0], values[0] + len(values))) and len(values) <= 7:
        rates = STANDARD_REPORT_RATES[: len(values)]
        candidates.append(
            PollingCodecHypothesis("ordinal_forward", dict(zip(values, rates)))
        )
        candidates.append(
            PollingCodecHypothesis("ordinal_reverse", dict(zip(values, reversed(rates))))
        )

    unique: dict[tuple[tuple[int, int], ...], PollingCodecHypothesis] = {}
    for candidate in candidates:
        key = tuple(sorted((int(raw), int(rate)) for raw, rate in candidate.mapping.items()))
        unique.setdefault(key, candidate)
    return tuple(unique.values())


def fit_linear_codec(pairs: Iterable[tuple[int, int]]) -> CodecSpec | None:
    """Fit ``semantic = raw * scale + offset`` to two or more exact observations."""

    observations = tuple((int(raw), int(value)) for raw, value in pairs)
    if len(observations) < 2:
        return None
    (raw_a, value_a), (raw_b, value_b) = observations[:2]
    raw_delta = raw_b - raw_a
    value_delta = value_b - value_a
    if raw_delta == 0 or value_delta % raw_delta:
        return None
    scale = value_delta // raw_delta
    offset = value_a - raw_a * scale
    if all(raw * scale + offset == value for raw, value in observations):
        return CodecSpec(CodecKind.LINEAR, scale=scale, offset=offset)
    return None
