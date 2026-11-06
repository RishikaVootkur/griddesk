/// <reference types="vite/client" />

/** Replaced at build time by vite.config.ts: true for the static GitHub Pages build. */
declare const __STATIC_MODE__: boolean;

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  /** Development only: an operator token for the console. Never set it in a build you serve. */
  readonly VITE_OPERATOR_TOKEN?: string;
}
