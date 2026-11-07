// The operator console's bearer token. The operator pastes it into the console once per browser
// session; it is kept in sessionStorage, never in the bundle. For local development it can also
// come from VITE_OPERATOR_TOKEN, but never bake a real token into a build served to customers.

const KEY = "griddesk.operatorToken";
let entered: string | null = null;

export function operatorToken(): string {
  if (entered !== null) return entered;
  try {
    const saved = sessionStorage.getItem(KEY);
    if (saved) return saved;
  } catch {
    // storage unavailable: fall back to the dev variable
  }
  return import.meta.env.VITE_OPERATOR_TOKEN ?? "";
}

export function setOperatorToken(token: string): void {
  entered = token;
  try {
    if (token) sessionStorage.setItem(KEY, token);
    else sessionStorage.removeItem(KEY);
  } catch {
    // storage unavailable: the token lasts until the page reloads
  }
}
