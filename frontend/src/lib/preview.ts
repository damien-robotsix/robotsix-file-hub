export type PreviewKind = "image" | "pdf" | "text" | "unsupported";

export function classifyPreview(contentType: string | null): PreviewKind {
  if (!contentType) return "unsupported";
  if (contentType.startsWith("image/")) return "image";
  if (contentType === "application/pdf") return "pdf";
  if (
    contentType.startsWith("text/") ||
    contentType === "application/json" ||
    contentType === "application/javascript" ||
    contentType === "application/xml"
  ) {
    return "text";
  }
  return "unsupported";
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
