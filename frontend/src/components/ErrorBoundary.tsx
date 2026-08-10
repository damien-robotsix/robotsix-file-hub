import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("ErrorBoundary caught an error:", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback !== undefined) {
        return this.props.fallback;
      }

      return (
        <div
          role="alert"
          style={{
            padding: "2rem",
            textAlign: "center",
            fontFamily: "system-ui, sans-serif",
          }}
        >
          <h2 style={{ margin: "0 0 0.5rem", fontSize: "1.25rem" }}>
            Something went wrong
          </h2>
          <p style={{ margin: "0 0 1rem", color: "#666" }}>
            An unexpected error occurred. Please try refreshing the page.
          </p>
          <pre
            style={{
              display: "inline-block",
              maxWidth: "100%",
              overflow: "auto",
              padding: "0.75rem",
              background: "#f5f5f5",
              borderRadius: "4px",
              fontSize: "0.875rem",
              textAlign: "left",
            }}
          >
            {this.state.error?.message ?? "Unknown error"}
          </pre>
        </div>
      );
    }

    return this.props.children;
  }
}
