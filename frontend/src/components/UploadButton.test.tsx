import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UploadButton from "./UploadButton";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("UploadButton", () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    vi.restoreAllMocks();
    user = userEvent.setup();
  });

  // -- Render ---------------------------------------------------------------

  it("renders a button with the upload-btn class and '+ Upload' text", () => {
    render(<UploadButton onClick={() => {}} />);

    const button = screen.getByRole("button", { name: "+ Upload" });
    expect(button).toBeInTheDocument();
    expect(button).toHaveClass("upload-btn");
  });

  // -- Click-through --------------------------------------------------------

  it("invokes onClick exactly once when clicked", async () => {
    const onClick = vi.fn();
    render(<UploadButton onClick={onClick} />);

    await user.click(screen.getByRole("button", { name: "+ Upload" }));

    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
