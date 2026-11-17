import { describe, expect, it } from "vitest";
import { buildApiUrl, cleanBaseUrl } from "./api";

describe("api helper functions", () => {
  describe("cleanBaseUrl", () => {
    it("handles standard base url", () => {
      expect(cleanBaseUrl("http://localhost:8000")).toBe("http://localhost:8000");
    });

    it("strips trailing slashes", () => {
      expect(cleanBaseUrl("http://localhost:8000/")).toBe("http://localhost:8000");
      expect(cleanBaseUrl("http://localhost:8000///")).toBe("http://localhost:8000");
    });

    it("strips accidental /api/v1 suffix", () => {
      expect(cleanBaseUrl("http://localhost:8000/api/v1")).toBe("http://localhost:8000");
      expect(cleanBaseUrl("http://localhost:8000/api/v1/")).toBe("http://localhost:8000");
    });

    it("strips accidental /api suffix", () => {
      expect(cleanBaseUrl("http://localhost:8000/api")).toBe("http://localhost:8000");
      expect(cleanBaseUrl("http://localhost:8000/api/")).toBe("http://localhost:8000");
    });

    it("trims whitespace", () => {
      expect(cleanBaseUrl("   http://localhost:8000/   ")).toBe("http://localhost:8000");
    });
  });

  describe("buildApiUrl", () => {
    it("builds URL with leading slash", () => {
      const url = buildApiUrl("/api/v1/stats/overview");
      expect(url.pathname).toBe("/api/v1/stats/overview");
    });

    it("builds URL without leading slash", () => {
      const url = buildApiUrl("api/v1/stats/overview");
      expect(url.pathname).toBe("/api/v1/stats/overview");
    });

    it("appends query parameters correctly", () => {
      const url = buildApiUrl("/api/v1/stats/overview", {
        since: "2026-11-10T00:00:00Z",
        empty: "",
        undef: undefined,
      });
      expect(url.pathname).toBe("/api/v1/stats/overview");
      expect(url.searchParams.get("since")).toBe("2026-11-10T00:00:00Z");
      expect(url.searchParams.has("empty")).toBe(false);
      expect(url.searchParams.has("undef")).toBe(false);
    });
  });
});
