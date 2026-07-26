export const TOKEN_KEY = "robotsix-file-hub-token";

export function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getStoredToken(): string | null {
  return readToken();
}
