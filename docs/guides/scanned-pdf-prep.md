# Scanned PDF Preparation Guide

RobotSix File Hub can enrich scanned/image-based PDFs (documents that contain
images but no extractable text layer) by rendering each page and sending the
page images through a vision-capable LLM. This guide walks through the
requirements and how the feature behaves.

## Requirements

The system must have two prerequisites:

1. **`poppler-utils` system package** — provides `pdftoppm`, which `pdf2image`
   uses to render PDF pages.
   - **Debian/Ubuntu:** `apt-get install -y poppler-utils`
   - **Alpine:** `apk add poppler-utils`
   - **macOS (Homebrew):** `brew install poppler`
   - **Windows:** Download and install [poppler for Windows](https://github.com/oschwartz10612/poppler-windows/releases) and add its `bin` directory to `PATH`.

2. **Vision-capable LLM** — the enrichment tier shown in `config/config.json`
   must resolve to a model that supports multimodal (image) input. If images
   and scanned PDFs fail to enrich, check the tier level.

## What counts as "scanned"

A PDF is treated as scanned when `pypdf`'s `extract_text()` returns **no text
at all** (empty or whitespace-only) across all of its pages. Examples:

- Documents created directly from a scanner or copier
- Print-then-scan workflows
- Flattened PDFs where the original text layer was burned into page images

A PDF with any extractable text (even just a few words, headers, or metadata
strings) is **not** treated as scanned — it goes down the existing text-based
enrichment path. This means:

- Digital-first PDFs → text LLM path (fast, cheap)
- Scanned/image-only PDFs → page rendering + vision LLM path

## How enrichment works for scanned PDFs

1. **Detection** — `extract_text()` returns a special sentinel
   (`SCANNED_PDF_SENTINEL`) when pypdf finds no text.
2. **Page rendering** — `pdf2image` converts each page of the PDF to a PNG
   image in memory. No temporary files are written to disk.
3. **Vision enrichment** — each page image is sent through the same
   `call_llm_vision()` pipeline as `image/*` uploads. The prompt and model
   tier are identical to the image path.
4. **Merging** — per-page results are combined into a single enrichment:
   - **Summary:** page summaries joined with spaces
   - **Category:** first page's category (they should agree; if not, the first is used)
   - **Tags:** all tags are collected, deduplicated, presered in order, capped at 10

## Multi-page documents

Each page is enriched independently and results merged, rather than sending
all pages in one request. This keeps requests well within the vision model's
context window regardless of document length, avoiding truncation and
quality loss on later pages.

Example a 5-page scanned contract:

```
Page 1 image → vision LLM → {summary: "...", category: "document", tags: ["page1", "contract"]}
Page 2 image → vision LLM → {summary: "...", category: "document", tags: ["page2", "contract"]}
Page 3 image → vision LLM → {summary: "...", category: "document", tags: ["page3"]}
Page 4 image → vision LLM → {summary: "...", category: "document", tags: ["page4"]}
Page 5 image → vision LLM → {summary: "...", category: "document", tags: ["page5"]}

Merged: {summary: "... ... ... ... ...", category: "document", tags: ["page1", "contract", "page2", "page3", "page4", "page5"]}
```

## Failure modes

| Scenario | Behaviour |
|---|---|
| `poppler-utils` not installed | `pdf2image` raises → enrichment returns `None` fields, document is stored with no enrichment |
| Vision model unavailable | Vision call raises → same graceful degradation — stored with `None` fields |
| Mixed PDF (some text pages, some scanned) | If any page has text, the whole PDF uses the text path (no rendering). |
| Extremely large PDF | Each page is rendered and enriched individually; memory usage is per-page, not per-document, but very large PDFs may still hit limits |

## Checking which path a PDF took

Enrichment uses three distinct LLM paths:

| Path | Trigger | LLM input |
|---|---|---|
| Text | `extract_text()` returns non-empty text | Plain text of the document |
| Image | `IMAGE_SENTINEL` (image/* upload) | Raw image bytes |
| Scanned PDF | `SCANNED_PDF_SENTINEL` (empty text from PDF) | Per-page PNG bytes |

There is no indicator in the stored record distinguishing "enriched via
text" from "enriched via scanned-PDF vision" — the enrichment output is the
same shape (`summary`, `category`, `tags`, `embedding`) regardless of
path.

## Tips for good results

- **Tier level** — ensure `enrichment_llm_tier_level` resolves to a
  vision-capable model. If the default tier 1 is text-only, raise the tier.
- **Scan quality** — cleaner, higher-contrast scans (300+ DPI, black and
  white or grayscale) produce better vision results than blurry or
  low-contrast scans.
- **Mixed documents** — if a document contains both text layers and scanned
  pages, the text path will be used (any text at all routes the whole PDF
  to text). For true mixed-type documents, consider splitting: scan pages
  separately or create a text-layer PDF.
