import { describe, it, expect, beforeEach } from "vitest";
import { TOKEN_KEY, readToken } from "./tokenStorage";

describe("tokenStorage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe("TOKEN_KEY", () => {
    it("is the expected key name", () => {
      expect(TOKEN_KEY).toBe("robotsix-file-hub-token");
    });
  });

  describe("readToken", () => {
    it("returns null when no token is stored", () => {
      expect(readToken()).toBeNull();
    });

    it("returns the stored token", () => {
      const token = "eyJhbGciOiJIUzI1NiJ9.test";
      localStorage.setItem(TOKEN_KEY, token);
      expect(readToken()).toBe(token);
    });

    it("returns null when localStorage throws (e.g. in a restricted environment)", () => {
      // Simulate localStorage.getItem throwing by temporarily overriding it
      const original = localStorage.getItem;
      localStorage.getItem = () => {
        throw new Error("access denied");
      };
      try {
        expect(readToken()).toBeNull();
      } finally {
        localStorage.getItem = original;
      }
    });
  });
});
