import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { AuthProvider, useAuth } from "./AuthContext";

describe("AuthProvider / useAuth", () => {
  it("throws when used outside AuthProvider", () => {
    expect(() => renderHook(() => useAuth())).toThrow(
      "useAuth must be used within an AuthProvider",
    );
  });

  it("starts unauthenticated when no token is stored", () => {
    localStorage.clear();
    const { result } = renderHook(() => useAuth(), {
      wrapper: AuthProvider,
    });
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.token).toBeNull();
  });

  it("starts authenticated when a token is already stored", () => {
    localStorage.clear();
    const token = "stored-token-123";
    localStorage.setItem("robotsix-file-hub-token", token);
    const { result } = renderHook(() => useAuth(), {
      wrapper: AuthProvider,
    });
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.token).toBe(token);
  });

  it("login stores the token and sets authenticated", () => {
    localStorage.clear();
    const { result } = renderHook(() => useAuth(), {
      wrapper: AuthProvider,
    });
    act(() => {
      result.current.login("new-token-456");
    });
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.token).toBe("new-token-456");
    expect(localStorage.getItem("robotsix-file-hub-token")).toBe("new-token-456");
  });

  it("logout clears the token and sets unauthenticated", () => {
    localStorage.clear();
    localStorage.setItem("robotsix-file-hub-token", "token-before");
    const { result } = renderHook(() => useAuth(), {
      wrapper: AuthProvider,
    });
    act(() => {
      result.current.logout();
    });
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.token).toBeNull();
    expect(localStorage.getItem("robotsix-file-hub-token")).toBeNull();
  });
});
