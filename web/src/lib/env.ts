/** True in the GitHub Pages build: only the eval dashboard, data from ./eval-data.json. */
export const STATIC_MODE: boolean = __STATIC_MODE__;

/** True when `npm run dev:mock` is used: every API call is served from in-browser mock data. */
export const MOCK_MODE: boolean = import.meta.env.MODE === "mock";

export const API_BASE: string = (import.meta.env.VITE_API_BASE ?? "/api").replace(/\/$/, "");
