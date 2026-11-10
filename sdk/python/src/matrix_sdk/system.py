"""System telemetry (`sys/*` metrics): psutil and, when present, NVML via `pynvml`.

The collector takes injectable backends, each exposing `sample() -> dict[str, float]` (keys already
carrying the `sys/` prefix) and an optional `close()`. A failing backend is skipped, never raised.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger("matrix_sdk.system")
MB = 1024 * 1024


class Backend(Protocol):
    def sample(self) -> dict[str, float]: ...


class PsutilBackend:
    """CPU % (delta since the previous sample), memory, disk/net I/O since start (MB) and process RSS."""

    def __init__(self, psutil_module: Any = None) -> None:
        if psutil_module is None:
            import psutil as psutil_module
        self._ps = psutil_module
        self._proc = psutil_module.Process()
        psutil_module.cpu_percent(interval=None)  # primes the delta; the first call always returns 0.0
        self._disk0 = self._disk()
        self._net0 = self._net()

    def _disk(self):
        try:
            return self._ps.disk_io_counters()
        except Exception:  # noqa: BLE001 - unavailable in some containers
            return None

    def _net(self):
        try:
            return self._ps.net_io_counters()
        except Exception:  # noqa: BLE001
            return None

    def sample(self) -> dict[str, float]:
        out: dict[str, float] = {"sys/cpu.percent": float(self._ps.cpu_percent(interval=None))}
        out["sys/memory.used_mb"] = self._ps.virtual_memory().used / MB
        disk = self._disk()
        if disk is not None and self._disk0 is not None:
            out["sys/disk.read_mb"] = max(0, disk.read_bytes - self._disk0.read_bytes) / MB
            out["sys/disk.write_mb"] = max(0, disk.write_bytes - self._disk0.write_bytes) / MB
        net = self._net()
        if net is not None and self._net0 is not None:
            out["sys/net.sent_mb"] = max(0, net.bytes_sent - self._net0.bytes_sent) / MB
            out["sys/net.recv_mb"] = max(0, net.bytes_recv - self._net0.bytes_recv) / MB
        with contextlib.suppress(Exception):
            out["sys/proc.rss_mb"] = self._proc.memory_info().rss / MB
        return out


class NvmlBackend:
    """Per GPU: utilization %, memory used (MB), temperature (C), power (W)."""

    def __init__(self, nvml: Any) -> None:
        self._nvml = nvml
        nvml.nvmlInit()
        self._handles = [nvml.nvmlDeviceGetHandleByIndex(i) for i in range(nvml.nvmlDeviceGetCount())]

    @classmethod
    def create(cls, nvml: Any = None) -> NvmlBackend | None:
        """None when `pynvml` is missing, there is no driver, or no GPU."""
        try:
            if nvml is None:
                import pynvml as nvml
            backend = cls(nvml)
        except Exception:  # noqa: BLE001 - ImportError, NVMLError_LibraryNotFound, driver errors...
            return None
        if not backend._handles:
            backend.close()
            return None
        return backend

    def sample(self) -> dict[str, float]:
        nv = self._nvml
        out: dict[str, float] = {}
        for i, h in enumerate(self._handles):
            p = f"sys/gpu.{i}."
            readers: dict[str, Callable[[], float]] = {
                "utilization": lambda h=h: nv.nvmlDeviceGetUtilizationRates(h).gpu,
                "memory_used_mb": lambda h=h: nv.nvmlDeviceGetMemoryInfo(h).used / MB,
                "temperature_c": lambda h=h: nv.nvmlDeviceGetTemperature(h, getattr(nv, "NVML_TEMPERATURE_GPU", 0)),
                "power_w": lambda h=h: nv.nvmlDeviceGetPowerUsage(h) / 1000.0,  # mW
            }
            for name, read in readers.items():
                with contextlib.suppress(Exception):
                    out[p + name] = float(read())
        return out

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._nvml.nvmlShutdown()


def default_backends() -> list[Any]:
    backends: list[Any] = []
    try:
        backends.append(PsutilBackend())
    except Exception:
        log.debug("psutil telemetry unavailable", exc_info=True)
    gpu = NvmlBackend.create()
    if gpu is not None:
        backends.append(gpu)
    return backends


class SystemMonitor:
    """Samples every `interval` seconds on a daemon thread; step = whole seconds since start."""

    def __init__(
        self,
        sink: Callable[[dict[str, float], int], None],
        interval: float = 10.0,
        backends: list[Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self.interval = interval
        self._backends = default_backends() if backends is None else list(backends)
        self._clock = clock
        self._t0 = clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample_once(self) -> dict[str, float]:
        metrics: dict[str, float] = {}
        for backend in self._backends:
            try:
                metrics.update(backend.sample())
            except Exception:
                log.debug("telemetry backend %r failed", backend, exc_info=True)
        if metrics:
            self._sink(metrics, int(self._clock() - self._t0))
        return metrics

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.sample_once()

    def start(self) -> None:
        if self._thread is None and self._backends:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="matrix-system-metrics")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2.0)
        for backend in self._backends:
            close = getattr(backend, "close", None)
            if close is not None:
                close()
