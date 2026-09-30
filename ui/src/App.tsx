import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Sidebar } from "./components/Sidebar";
import { ToastProvider } from "./components/Toast";
import { TopBar } from "./components/TopBar";
import { EngineProvider, useEngine } from "./engine";
import { navigate, useRoute, type Route } from "./router";
import { Activity } from "./screens/Activity";
import { DealDetail } from "./screens/DealDetail";
import { Deals } from "./screens/Deals";
import { Destinations } from "./screens/Destinations";
import { Notifications } from "./screens/Notifications";
import { Settings } from "./screens/Settings";
import { Setup } from "./screens/Setup";
import { ThemeProvider } from "./theme";

function screenFor(route: Route): ReactNode {
  switch (route.screen) {
    case "destinations":
      return <Destinations focus={route.param?.toUpperCase()} />;
    case "activity":
      return <Activity />;
    case "notifications":
      return <Notifications />;
    case "settings":
      return <Settings section={route.param} />;
    case "setup":
      return <Setup />;
    default:  // a deal's details slide over the list, which stays as it was (and stays if they fail)
      return <><Deals />{route.dealId && <ErrorBoundary key={route.dealId} where="screen"><DealDetail id={route.dealId} /></ErrorBoundary>}</>;
  }
}

/** A new install (setup not finished) opens on the setup wizard, once, unless a screen was asked for. */
function FirstRun() {
  const { status } = useEngine();
  const done = useRef(false);
  useEffect(() => {
    if (!status || done.current) return;
    done.current = true;
    const hash = window.location.hash.replace(/^#\/?/, "");
    if (status.setup_needed && (hash === "" || hash === "deals")) navigate({ screen: "setup" });
  }, [status]);
  return null;
}

export function App() {
  const route = useRoute();
  // The engine marks <html data-sidebar> with how the sidebar was left, and keeps each change
  const [collapsed, setCollapsed] = useState(() => document.documentElement.dataset.sidebar === "collapsed");
  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    api.saveUi({ sidebar: next ? "collapsed" : "open" }).catch(() => {});  // not kept: it lasts until the app closes
  };
  return (
    <ThemeProvider>
      <EngineProvider>
        <ToastProvider>
        <FirstRun />
        <TopBar onToggleSidebar={toggle} />
        <Sidebar screen={route.screen} collapsed={collapsed} />
        <div className={`transition-[padding] ${collapsed ? "pl-18" : "pl-68"}`}>
          <main className="min-h-screen pt-16">
            <div className="@container mx-auto max-w-[1760px] p-space-xl">
              {/* A failing screen shows its error, not a blank window; another screen starts afresh */}
              <ErrorBoundary key={route.screen} where="screen">{screenFor(route)}</ErrorBoundary>
            </div>
          </main>
        </div>
        </ToastProvider>
      </EngineProvider>
    </ThemeProvider>
  );
}
