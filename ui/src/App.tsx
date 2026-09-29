import { useState, type ReactNode } from "react";
import { Sidebar } from "./components/Sidebar";
import { ToastProvider } from "./components/Toast";
import { TopBar } from "./components/TopBar";
import { EngineProvider } from "./engine";
import { useRoute, type Route } from "./router";
import { Activity } from "./screens/Activity";
import { DealDetail } from "./screens/DealDetail";
import { Deals } from "./screens/Deals";
import { Destinations } from "./screens/Destinations";
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
      return <div className="legacy"><Destinations /></div>;
    case "activity":
      return <div className="legacy"><Activity /></div>;
    case "settings":
      return <div className="legacy"><Settings /></div>;
    case "setup":
    case "notifications":
      return <div className="legacy"><Setup /></div>;
    default:  // a deal's details slide over the list, which stays as it was
      return <><Deals />{route.dealId && <DealDetail id={route.dealId} />}</>;
  }
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
