import { lazy, type ComponentType } from "react";
import { BrowserRouter, HashRouter, Navigate, Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { STATIC_MODE } from "./lib/env";

// Each view is its own chunk, so the chat page does not download the chart library.
// The static (GitHub Pages) build drops the chat and operator code entirely.
const noView = (): Promise<{ default: ComponentType }> => Promise.resolve({ default: () => null });
const ChatView = lazy(__STATIC_MODE__ ? noView : () => import("./views/chat/ChatView").then((m) => ({ default: m.ChatView })));
const OperatorView = lazy(
  __STATIC_MODE__ ? noView : () => import("./views/operator/OperatorView").then((m) => ({ default: m.OperatorView })),
);
const EvalsView = lazy(() => import("./views/evals/EvalsView").then((m) => ({ default: m.EvalsView })));
const CompareView = lazy(() => import("./views/evals/CompareView").then((m) => ({ default: m.CompareView })));

// GitHub Pages cannot rewrite deep links, so the static build uses hash URLs.
const Router = STATIC_MODE ? HashRouter : BrowserRouter;

export function App() {
  return (
    <Router>
      <Routes>
        <Route element={<Layout />}>
          {STATIC_MODE ? (
            <Route index element={<Navigate to="/evals" replace />} />
          ) : (
            <>
              <Route index element={<ChatView />} />
              <Route path="operator" element={<OperatorView />} />
            </>
          )}
          <Route path="evals" element={<EvalsView />} />
          <Route path="evals/compare" element={<CompareView />} />
          <Route path="*" element={<Navigate to={STATIC_MODE ? "/evals" : "/"} replace />} />
        </Route>
      </Routes>
    </Router>
  );
}
