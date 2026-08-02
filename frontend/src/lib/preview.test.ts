import { describe, it, expect } from "vitest";
import { classifyPreview, escapeHtml } from "./preview";

describe("classifyPreview", () => {
  it("returns unsupported for null content type", () => {
    expect(classifyPreview(null)).toBe("unsupported");
  });

  it("returns image for image/* content types", () => {
    expect(classifyPreview("image/png")).toBe("image");
    expect(classifyPreview("image/jpeg")).toBe("image");
    expect(classifyPreview("image/svg+xml")).toBe("image");
  });

  it("returns pdf for application/pdf", () => {
    expect(classifyPreview("application/pdf")).toBe("pdf");
  });

  it("returns text for text/* content types", () => {
    expect(classifyPreview("text/plain")).toBe("text");
    expect(classifyPreview("text/html")).toBe("text");
    expect(classifyPreview("text/csv")).toBe("text");
  });

  it("returns text for application/json, javascript, xml", () => {
    expect(classifyPreview("application/json")).toBe("text");
    expect(classifyPreview("application/javascript")).toBe("text");
    expect(classifyPreview("application/xml")).toBe("text");
  });

  it("returns unsupported for unknown content types", () => {
    expect(classifyPreview("application/octet-stream")).toBe("unsupported");
    expect(classifyPreview("video/mp4")).toBe("unsupported");
  });
});

describe("escapeHtml", () => {
  it("escapes ampersands", () => {
    expect(escapeHtml("a & b")).toBe("a &amp; b");
  });

  it("escapes less-than and greater-than", () => {
    expect(escapeHtml("<div>")).toBe("&lt;div&gt;");
  });

  it("escapes double quotes", () => {
    expect(escapeHtml('"hello"')).toBe("&quot;hello&quot;");
  });

  it("returns plain text unchanged", () => {
    expect(escapeHtml("hello world")).toBe("hello world");
  });

  it("escapes multiple special characters in one string", () => {
    expect(escapeHtml('<a href="x & y">')).toBe(
      "&lt;a href=&quot;x &amp; y&quot;&gt;",
    );
  });
});
