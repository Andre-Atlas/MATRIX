import { describe, expect, it } from "vitest";
import { formatBytes, formatMegabytes, formatMetric, formatPower, formatSeconds, formatSystemValue, runDuration, shortCommit } from "./format";

describe("durations", () => {
  it("formatSeconds", () => {
    expect(formatSeconds(0.25)).toBe("250 ms");
    expect(formatSeconds(42.7)).toBe("42s");
    expect(formatSeconds(185)).toBe("3m 05s");
    expect(formatSeconds(2 * 3600 + 14 * 60 + 9)).toBe("2h 14m");
    expect(formatSeconds(3 * 86400 + 4 * 3600)).toBe("3d 4h");
    expect(formatSeconds(-1)).toBe("—");
  });
  it("runDuration uses now while running", () => {
    const start = "2026-11-10T10:00:00Z";
    expect(runDuration(start, "2026-11-10T10:01:30Z")).toBe("1m 30s");
    expect(runDuration(start, null, Date.parse("2026-11-10T12:00:00Z"))).toBe("2h 00m");
    expect(runDuration("bad", null)).toBe("—");
  });
});

describe("units", () => {
  it("formatPower", () => {
    expect(formatPower(245.4)).toBe("245 W");
    expect(formatPower(61.25)).toBe("61.3 W");
    expect(formatPower(1200)).toBe("1.20 kW");
    expect(formatPower(null)).toBe("—");
  });
  it("formatBytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.50 KiB");
    expect(formatBytes(12 * 1024 * 1024)).toBe("12.0 MiB");
    expect(formatBytes(3.25 * 1024 ** 3)).toBe("3.25 GiB");
    expect(formatBytes(-1)).toBe("—");
    expect(formatMegabytes(40_960)).toBe("40.0 GiB");
  });
  it("formatMetric", () => {
    expect(formatMetric(0.123456)).toBe("0.1235");
    expect(formatMetric(3e-5)).toBe("3.00e-5");
    expect(formatMetric(1200)).toBe("1200");
    expect(formatMetric(NaN)).toBe("NaN");
    expect(formatMetric(-Infinity)).toBe("−∞");
    expect(formatMetric(null)).toBe("—");
  });
  it("system values by suffix", () => {
    expect(formatSystemValue("sys/gpu.0.power_w", 300)).toBe("300 W");
    expect(formatSystemValue("sys/gpu.0.temperature_c", 71.6)).toBe("72 °C");
    expect(formatSystemValue("sys/cpu.percent", 55.2)).toBe("55%");
    expect(formatSystemValue("sys/memory.used_mb", 1024)).toBe("1.00 GiB");
    expect(shortCommit("0123456789abcdef")).toBe("0123456");
  });
});
