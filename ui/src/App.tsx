import { StatusBar } from "./components/StatusBar";
import { href, useRoute, type Screen } from "./router";
import { Activity } from "./screens/Activity";
import { DealDetail } from "./screens/DealDetail";
import { Deals } from "./screens/Deals";
import { Destinations } from "./screens/Destinations";
import { Settings } from "./screens/Settings";
import { Setup } from "./screens/Setup";

const NAV: { screen: Screen; label: string }[] = [
  { screen: "deals", label: "Deals" },
  { screen: "destinations", label: "Destinations" },
  { screen: "activity", label: "Activity" },
  { screen: "settings", label: "Settings" },
  { screen: "setup", label: "Setup checks" },
];

export function App() {
  const route = useRoute();
  return (
    <div className="app">
      <nav className="sidebar" aria-label="Screens">
        <div className="brand">CheapTrip</div>
        {NAV.map((item) => (
          <a key={item.screen} href={href({ screen: item.screen })}
             className={route.screen === item.screen ? "active" : ""}
             aria-current={route.screen === item.screen ? "page" : undefined}>{item.label}</a>
        ))}
      </nav>
      <div className="main">
        <StatusBar />
        <main>
          {route.dealId ? <DealDetail id={route.dealId} />
            : route.screen === "destinations" ? <Destinations />
              : route.screen === "activity" ? <Activity />
                : route.screen === "settings" ? <Settings />
                  : route.screen === "setup" ? <Setup />
                    : <Deals />}
        </main>
      </div>
    </div>
  );
}
