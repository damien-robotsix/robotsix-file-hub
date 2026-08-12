import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import ErrorBoundary from "./ErrorBoundary";

// ---------------------------------------------------------------------------
// Test component that throws during render
// ---------------------------------------------------------------------------

function Bomb({ message }: { message?: string }): never {
  throw new Error(message ?? "Boom!");
}

function Safe({ text }: { text: string }) {
  return <p>{text}</p>;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ErrorBoundary", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // -- Render children normally --------------------------------------------

  it("renders children when there is no error", () => {
    render(
      <ErrorBoundary>
        <Safe text="hello" />
      </ErrorBoundary>,
    );

    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  // -- Catch error & show default fallback ---------------------------------

  it("renders default fallback UI when a child throws", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText("Boom!")).toBeInTheDocument();

    spy.mockRestore();
  });

  // -- Custom fallback -----------------------------------------------------

  it("renders custom fallback when provided", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary fallback={<h1>Custom error UI</h1>}>
        <Bomb />
      </ErrorBoundary>,
    );

    expect(screen.getByText("Custom error UI")).toBeInTheDocument();
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();

    spy.mockRestore();
  });

  // -- Error message in default fallback -----------------------------------

  it("shows 'Unknown error' when error has no message", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <Bomb message="" />
      </ErrorBoundary>,
    );

    expect(screen.getByText("Unknown error")).toBeInTheDocument();

    spy.mockRestore();
  });

  // -- Logs error to console -----------------------------------------------

  it("logs the error to console.error", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <Bomb message="logged-error" />
      </ErrorBoundary>,
    );

    expect(spy).toHaveBeenCalled();
    const logged = spy.mock.calls.some(
      (call) =>
        typeof call[0] === "string" &&
        call[0].includes("ErrorBoundary caught an error:"),
    );
    expect(logged).toBe(true);

    spy.mockRestore();
  });
});
