import { useEffect, useRef, useState, type ReactNode } from "react";
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

const SIDEBAR_KEY = "cheaptrip.sidebar";

function stored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // private window: the choice lasts until the app closes
  }
}

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
    default:  // a deal's details slide over the list, which stays as it was
      return <><Deals />{route.dealId && <DealDetail id={route.dealId} />}</>;
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
  const [collapsed, setCollapsed] = useState(() => stored(SIDEBAR_KEY) === "collapsed");
  const toggle = () => setCollapsed((c) => {
    store(SIDEBAR_KEY, c ? "open" : "collapsed");
    return !c;
  });
  return (
    <ThemeProvider>
      <EngineProvider>
        <ToastProvider>
        <FirstRun />
        <TopBar onToggleSidebar={toggle} />
        <Sidebar screen={route.screen} collapsed={collapsed} />
        <div className={`transition-[padding] ${collapsed ? "pl-16" : "pl-60"}`}>
          <main className="min-h-screen pt-14">
            <div className="p-space-lg">{screenFor(route)}</div>
          </main>
        </div>
        </ToastProvider>
      </EngineProvider>
    </ThemeProvider>
  );
}
