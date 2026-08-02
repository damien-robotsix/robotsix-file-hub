import { describe, it, expect } from "vitest";
import { formatSize } from "./format";

describe("formatSize", () => {
  it("formats bytes under 1 KB as plain bytes", () => {
    expect(formatSize(0)).toBe("0 B");
    expect(formatSize(1)).toBe("1 B");
    expect(formatSize(1023)).toBe("1023 B");
  });

  it("formats kilobytes with one decimal place", () => {
    expect(formatSize(1024)).toBe("1.0 KB");
    expect(formatSize(1536)).toBe("1.5 KB");
    expect(formatSize(1047552)).toBe("1023.0 KB");
  });

  it("formats megabytes with one decimal place", () => {
    expect(formatSize(1048576)).toBe("1.0 MB");
    expect(formatSize(1572864)).toBe("1.5 MB");
    expect(formatSize(1073741824)).toBe("1024.0 MB");
  });
});
