"""Optional, non-authoritative Trace export for the W3 runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import importlib
from typing import Protocol


TraceValue = str | int | float | bool | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TraceRecord:
    name: str
    attributes: Mapping[str, TraceValue]


class TraceExporter(Protocol):
    def export(self, record: TraceRecord) -> None: ...


class TraceDependencyUnavailable(RuntimeError):
    """Raised by an optional exporter whose dependency is unavailable."""


class OpenTelemetryTraceExporter:
    """Small optional bridge; OpenTelemetry remains outside core dependencies."""

    def __init__(
        self,
        *,
        instrumentation_name: str = "conflux-weave",
        module_loader: Callable[[str], object] = importlib.import_module,
    ) -> None:
        self.instrumentation_name = instrumentation_name
        self._module_loader = module_loader

    def export(self, record: TraceRecord) -> None:
        try:
            trace = self._module_loader("opentelemetry.trace")
        except (ImportError, ModuleNotFoundError) as exc:
            raise TraceDependencyUnavailable(
                "OpenTelemetry is not installed"
            ) from exc
        tracer = trace.get_tracer(self.instrumentation_name)
        with tracer.start_as_current_span(record.name) as span:
            for key, value in record.attributes.items():
                span.set_attribute(key, value)


class SafeTraceExporter:
    """Failure-isolating wrapper; Trace never owns Runtime success."""

    def __init__(
        self,
        exporter: TraceExporter,
        *,
        on_drop: Callable[[TraceRecord, str], None] | None = None,
    ) -> None:
        self.exporter = exporter
        self.on_drop = on_drop
        self.drop_count = 0

    def export(self, record: TraceRecord) -> bool:
        try:
            self.exporter.export(record)
        except Exception as exc:
            self.drop_count += 1
            if self.on_drop is not None:
                try:
                    self.on_drop(record, type(exc).__name__)
                except Exception:
                    pass
            return False
        return True
