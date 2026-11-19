import sys
from types import SimpleNamespace

from matrix_sdk.system import MB, NvmlBackend, PsutilBackend, SystemMonitor, default_backends

PSUTIL_KEYS = {"sys/cpu.percent", "sys/memory.used_mb", "sys/proc.rss_mb"}


class FakeNvml:
    """Mimics the pynvml calls the backend uses; GPU 1 does not report power."""

    NVML_TEMPERATURE_GPU = 0

    def __init__(self, count=2):
        self.count = count
        self.inited = self.shut = False

    def nvmlInit(self):
        self.inited = True

    def nvmlShutdown(self):
        self.shut = True

    def nvmlDeviceGetCount(self):
        return self.count

    def nvmlDeviceGetHandleByIndex(self, i):
        return i

    def nvmlDeviceGetUtilizationRates(self, h):
        return SimpleNamespace(gpu=50 + h, memory=10)

    def nvmlDeviceGetMemoryInfo(self, h):
        return SimpleNamespace(used=(h + 1) * 1024 * MB, total=16 * 1024 * MB)

    def nvmlDeviceGetTemperature(self, h, sensor):
        assert sensor == self.NVML_TEMPERATURE_GPU
        return 60 + h

    def nvmlDeviceGetPowerUsage(self, h):
        if h == 1:
            raise RuntimeError("NVML_ERROR_NOT_SUPPORTED")
        return 150_500  # mW


def test_psutil_backend_keys():
    sample = PsutilBackend().sample()
    assert PSUTIL_KEYS <= set(sample)
    assert all(k.startswith("sys/") for k in sample)
    assert sample["sys/memory.used_mb"] > 0 and sample["sys/proc.rss_mb"] > 0
    for key in ("sys/disk.read_mb", "sys/disk.write_mb", "sys/net.sent_mb", "sys/net.recv_mb"):
        if key in sample:  # some containers expose no counters
            assert sample[key] >= 0


def test_psutil_deltas_since_start():
    counters = {"disk": SimpleNamespace(read_bytes=10 * MB, write_bytes=0),
                "net": SimpleNamespace(bytes_sent=0, bytes_recv=5 * MB)}
    calls = []

    class FakePs:
        def Process(self):
            return SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=200 * MB))

        def cpu_percent(self, interval):
            calls.append(interval)
            return 42.0

        def virtual_memory(self):
            return SimpleNamespace(used=1024 * MB)

        def disk_io_counters(self):
            return counters["disk"]

        def net_io_counters(self):
            return counters["net"]

    backend = PsutilBackend(FakePs())
    counters["disk"] = SimpleNamespace(read_bytes=13 * MB, write_bytes=2 * MB)
    counters["net"] = SimpleNamespace(bytes_sent=MB, bytes_recv=6 * MB)
    s = backend.sample()
    assert calls == [None, None]  # non-blocking delta mode, primed once at construction
    assert s == {"sys/cpu.percent": 42.0, "sys/memory.used_mb": 1024.0, "sys/disk.read_mb": 3.0,
                 "sys/disk.write_mb": 2.0, "sys/net.sent_mb": 1.0, "sys/net.recv_mb": 1.0,
                 "sys/proc.rss_mb": 200.0}


def test_nvml_per_gpu_keys():
    nvml = FakeNvml()
    backend = NvmlBackend.create(nvml)
    s = backend.sample()
    assert s["sys/gpu.0.utilization"] == 50 and s["sys/gpu.1.utilization"] == 51
    assert s["sys/gpu.0.memory_used_mb"] == 1024 and s["sys/gpu.1.memory_used_mb"] == 2048
    assert s["sys/gpu.0.temperature_c"] == 60 and s["sys/gpu.1.temperature_c"] == 61
    assert s["sys/gpu.0.power_w"] == 150.5 and "sys/gpu.1.power_w" not in s
    backend.close()
    assert nvml.inited and nvml.shut


def test_nvml_absent_is_silent(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)  # import raises ImportError
    assert NvmlBackend.create() is None
    assert NvmlBackend.create(FakeNvml(count=0)) is None

    class Broken(FakeNvml):
        def nvmlInit(self):
            raise OSError("NVML Shared Library Not Found")

    assert NvmlBackend.create(Broken()) is None
    assert any(isinstance(b, PsutilBackend) for b in default_backends())


def test_monitor_steps_are_whole_seconds_and_failures_skipped():
    now = [100.0]
    got = []

    class Bad:
        def sample(self):
            raise RuntimeError("x")

    nv = NvmlBackend(FakeNvml(count=1))
    mon = SystemMonitor(lambda m, step: got.append((step, m)), interval=10, backends=[Bad(), nv],
                        clock=lambda: now[0])
    mon.sample_once()
    now[0] = 112.7
    mon.sample_once()
    assert [s for s, _ in got] == [0, 12]
    assert "sys/gpu.0.utilization" in got[1][1]
    mon.stop()


def test_monitor_thread_runs_and_stops():
    got = []

    class B:
        def sample(self):
            return {"sys/cpu.percent": 1.0}

    mon = SystemMonitor(lambda m, s: got.append(m), interval=0.01, backends=[B()])
    mon.start()
    import time

    deadline = time.monotonic() + 2
    while not got and time.monotonic() < deadline:
        time.sleep(0.01)
    mon.stop()
    assert got and not mon._thread.is_alive()
