import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import ErrorBoundary from "./components/ErrorBoundary.tsx";
import App from "./App.tsx";
import "./index.css";

createRoot(document.getElementById("root")!, {
  onCaughtError(_error, errorInfo) {
    console.error("React caught error:", errorInfo.componentStack);
  },
  onUncaughtError(error, errorInfo) {
    console.error("React uncaught error:", error, errorInfo.componentStack);
  },
}).render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
);
