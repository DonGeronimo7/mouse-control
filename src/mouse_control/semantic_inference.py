"""Evidence-producing semantic inference for unknown mouse protocols.

Inference is deliberately conservative: it labels hypotheses and candidate
codecs, never writable capabilities. Guided discovery can accumulate these
hypotheses until repeated observations or a proven repertoire family validates
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .event_correlation import CorrelationCandidate
from .protocol_codec import fit_linear_codec, infer_polling_codecs
from .protocol_grammar import CodecSpec, SemanticBehavior


@dataclass(frozen=True)
class SemanticHypothesis:
    behavior: SemanticBehavior
    confidence: str
    reason: str
    report_key: object | None = None
    offset: int | None = None
    codec: CodecSpec | None = None
    mapping: Mapping[int, int] | None = None


def _looks_cyclic(values: tuple[int, ...], transitions: tuple[tuple[int | None, int | None], ...]) -> bool:
    if len(values) < 2 or len(values) > 16:
        return False
    concrete = [(before, after) for before, after in transitions if before is not None and after is not None]
    if len(concrete) < 2:
        return False
    value_set = set(values)
    return all(before in value_set and after in value_set and before != after for before, after in concrete)


def infer_stage_hypotheses(
    candidates: Iterable[CorrelationCandidate],
) -> tuple[SemanticHypothesis, ...]:
    """Identify small repeated/cyclic state fields as stage-index candidates."""

    result: list[SemanticHypothesis] = []
    for candidate in candidates:
        if candidate.observations < 2:
            continue
        if not _looks_cyclic(candidate.values, candidate.transitions):
            continue
        result.append(
            SemanticHypothesis(
                behavior=SemanticBehavior.DPI_STAGE_INDEX,
                confidence="correlated",
                reason=(
                    f"field changed across {candidate.observations} repeated physical actions "
                    f"within a small {len(candidate.values)}-value state set"
                ),
                report_key=candidate.report_key,
                offset=candidate.offset,
            )
        )
    return tuple(result)


def infer_trigger_hypotheses(
    candidates: Iterable[CorrelationCandidate],
    *,
    behavior: SemanticBehavior,
) -> tuple[SemanticHypothesis, ...]:
    """Label repeated momentary transitions using an explicitly guided action.

    This does not infer what a random button means. The caller supplies the
    semantic label because the user was explicitly asked to perform that action
    (for example, press the physical DPI-cycle button once per sample).
    """

    result: list[SemanticHypothesis] = []
    for candidate in candidates:
        if candidate.observations < 2 or len(candidate.values) < 2:
            continue
        result.append(
            SemanticHypothesis(
                behavior=behavior,
                confidence="correlated",
                reason=(
                    f"momentary raw transition repeated in {candidate.observations} "
                    "guided samples of the labelled physical action"
                ),
                report_key=candidate.report_key,
                offset=candidate.offset,
            )
        )
    return tuple(result)


def infer_polling_hypotheses(
    raw_values: Iterable[int],
    *,
    report_key: object | None = None,
    offset: int | None = None,
) -> tuple[SemanticHypothesis, ...]:
    """Map observed raw states onto common gaming-mouse report-rate encodings."""

    result: list[SemanticHypothesis] = []
    for hypothesis in infer_polling_codecs(raw_values):
        result.append(
            SemanticHypothesis(
                behavior=SemanticBehavior.REPORT_RATE_HZ,
                confidence="correlated",
                reason=f"raw states fit common polling codec {hypothesis.name}",
                report_key=report_key,
                offset=offset,
                mapping=dict(hypothesis.mapping),
            )
        )
    return tuple(result)


def infer_dpi_codec_hypotheses(
    observations: Iterable[tuple[int, int]],
    *,
    report_key: object | None = None,
    offset: int | None = None,
) -> tuple[SemanticHypothesis, ...]:
    """Fit exact raw->DPI observations to common linear sensor encodings."""

    pairs = tuple((int(raw), int(dpi)) for raw, dpi in observations)
    if len(pairs) < 2:
        return ()
    result: list[SemanticHypothesis] = []
    fitted = fit_linear_codec(pairs)
    if fitted is not None:
        result.append(
            SemanticHypothesis(
                behavior=SemanticBehavior.DPI_VALUE,
                confidence="validated" if len(pairs) >= 3 else "correlated",
                reason=(
                    f"{len(pairs)} exact raw/DPI observations fit one linear codec "
                    f"(scale={fitted.scale}, offset={fitted.offset})"
                ),
                report_key=report_key,
                offset=offset,
                codec=fitted,
            )
        )
    return tuple(result)