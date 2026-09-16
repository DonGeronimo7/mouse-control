"""Protocol-neutral transaction state machine for repertoire operations.

The engine owns sequencing, waits, retries, prerequisites and safety gating.
A transport adapter owns the actual bytes.  This split lets HID++, Razer-style
RPC, selector/config-blob protocols, BLE adapters and future learned families
share one execution model without making discovery itself vendor-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Mapping, MutableMapping, Protocol

from .protocol_grammar import SafetyClass, TransactionSpec, TransactionStep, TransportKind


class TransactionError(RuntimeError):
    """A repertoire transaction failed."""


class TransactionAuthorizationError(TransactionError):
    """A transaction was blocked by the protocol safety policy."""


class RetryableTransactionError(TransactionError):
    """The current step may be retried within its declared retry budget."""


@dataclass(frozen=True)
class TransactionAuthorization:
    """Explicit execution authority supplied by a proven matcher/backend."""

    passive_reads: bool = True
    active_queries: bool = False
    reversible_writes: bool = False
    persistent_writes: bool = False
    dangerous_operations: bool = False
    reason: str = ""


@dataclass
class TransactionContext:
    values: MutableMapping[str, Any] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    trace: list[tuple[str, str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class StepResult:
    value: Any = None
    status: str = "success"


class TransactionAdapter(Protocol):
    """Transport-specific implementation used by :class:`TransactionEngine`."""

    def execute(self, step: TransactionStep, context: TransactionContext) -> StepResult | Any:
        ...

    def condition(self, expression: str, result: Any, context: TransactionContext) -> bool:
        ...

    def verify(self, rule: str, context: TransactionContext) -> bool:
        ...


_WRITE_TRANSPORTS = {
    TransportKind.HID_OUTPUT,
    TransportKind.HID_FEATURE_SET,
    TransportKind.USB_CONTROL,
    TransportKind.BLE_GATT_WRITE,
}


class TransactionEngine:
    """Execute declarative protocol state machines under an explicit safety gate."""

    def __init__(self, *, sleep=time.sleep) -> None:
        self._sleep = sleep

    @staticmethod
    def _contains_write_transport(spec: TransactionSpec) -> bool:
        return any(step.transport in _WRITE_TRANSPORTS for step in spec.steps)

    @staticmethod
    def _authorize(spec: TransactionSpec, authorization: TransactionAuthorization) -> None:
        if spec.safety is SafetyClass.DANGEROUS:
            if not authorization.dangerous_operations:
                raise TransactionAuthorizationError(
                    f"{spec.name}: dangerous transaction is not authorized"
                )
            return
        if spec.safety is SafetyClass.PERSISTENT:
            if not authorization.persistent_writes:
                raise TransactionAuthorizationError(
                    f"{spec.name}: persistent write is not authorized"
                )
            return
        if spec.safety is SafetyClass.REVERSIBLE:
            if not authorization.reversible_writes:
                raise TransactionAuthorizationError(
                    f"{spec.name}: reversible write is not authorized"
                )
            return
        if spec.safety is SafetyClass.UNKNOWN:
            raise TransactionAuthorizationError(
                f"{spec.name}: unknown-safety transaction is never executed automatically"
            )

        # A logical read/query may need a SET_FEATURE heartbeat or selector.
        # Those active queries are still blocked for an unknown family unless a
        # proven backend/exact-family rule explicitly authorizes them.
        if TransactionEngine._contains_write_transport(spec):
            if not authorization.active_queries:
                raise TransactionAuthorizationError(
                    f"{spec.name}: active query requires explicit authorization"
                )
        elif not authorization.passive_reads:
            raise TransactionAuthorizationError(
                f"{spec.name}: passive reads are not authorized"
            )

    def run(
        self,
        spec: TransactionSpec,
        adapter: TransactionAdapter,
        *,
        authorization: TransactionAuthorization = TransactionAuthorization(),
        context: TransactionContext | None = None,
    ) -> TransactionContext:
        """Execute ``spec`` and return the accumulated transaction context."""

        self._authorize(spec, authorization)
        ctx = context or TransactionContext()
        missing = tuple(name for name in spec.prerequisite if name not in ctx.completed)
        if missing:
            raise TransactionError(
                f"{spec.name}: missing prerequisite transaction(s): {', '.join(missing)}"
            )

        for index, step in enumerate(spec.steps):
            if step.operation == "wait":
                if step.delay_ms:
                    self._sleep(step.delay_ms / 1000.0)
                ctx.trace.append((spec.name, f"step-{index}:wait", step.delay_ms))
                continue

            attempts = step.retries + 1
            last_error: Exception | None = None
            result: Any = None
            for attempt in range(attempts):
                try:
                    raw_result = adapter.execute(step, ctx)
                    result = raw_result.value if isinstance(raw_result, StepResult) else raw_result
                    status = raw_result.status if isinstance(raw_result, StepResult) else "success"
                    if status == "retry":
                        raise RetryableTransactionError(
                            f"{spec.name}: step {index} requested retry"
                        )
                    if status not in {"success", "ok"}:
                        raise TransactionError(
                            f"{spec.name}: step {index} returned status {status!r}"
                        )
                    if step.condition and not adapter.condition(step.condition, result, ctx):
                        raise RetryableTransactionError(
                            f"{spec.name}: step {index} condition {step.condition!r} not met"
                        )
                    last_error = None
                    break
                except RetryableTransactionError as exc:
                    last_error = exc
                    if attempt + 1 >= attempts:
                        break
                    if step.delay_ms:
                        self._sleep(step.delay_ms / 1000.0)

            if last_error is not None:
                raise TransactionError(
                    f"{spec.name}: step {index} exhausted {attempts} attempt(s): {last_error}"
                ) from last_error

            ctx.trace.append((spec.name, f"step-{index}:{step.operation}", result))
            if step.delay_ms:
                self._sleep(step.delay_ms / 1000.0)

        for rule in spec.verification:
            if not adapter.verify(rule, ctx):
                raise TransactionError(f"{spec.name}: verification failed: {rule}")

        ctx.completed.add(spec.name)
        return ctx
