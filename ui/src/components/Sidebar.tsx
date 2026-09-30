/**
 * The navigation pane: Monitor (Deals, Destinations, Activity) and Configuration
 * (Settings, Setup wizard, Notifications), collapsible to icons; at the bottom
 * the engine's state, the version and "hide to tray".
 */
import { api } from "../api";
import { useEngine } from "../engine";
import { useReloadOn } from "../events";
import { clock } from "../format";
import { useLoad } from "../load";
import { href, type Screen } from "../router";
import { Icon } from "./Icon";
import { StateDot } from "./TopBar";

interface NavItem {
  screen: Screen;
  label: string;
  icon: string;
}

const MONITOR: NavItem[] = [
  { screen: "deals", label: "Deals", icon: "local_offer" },
  { screen: "destinations", label: "Destinations", icon: "map" },
  { screen: "activity", label: "Activity", icon: "monitoring" },
];
const CONFIGURATION: NavItem[] = [
  { screen: "settings", label: "Settings", icon: "settings" },
  { screen: "setup", label: "Setup wizard", icon: "magic_button" },
  { screen: "notifications", label: "Notifications", icon: "notifications_active" },
];

const ENGINE_LABELS = {
  running: "Watching prices", searching: "Searching…", paused: "Alerts paused",
  attention: "Needs attention", stopped: "Stopped",
} as const;

function NavLink({ item, current, collapsed, badge }: {
  item: NavItem; current: boolean; collapsed: boolean; badge?: number;
}) {
  return (
    <a href={href({ screen: item.screen })} aria-current={current ? "page" : undefined}
       title={collapsed ? item.label : undefined}
       className={`flex h-11 items-center justify-between rounded-xl px-space-md transition-colors ${current
         ? "bg-primary-container font-semibold text-on-primary"
         : "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"}`}>
      <span className="flex items-center gap-space-md">
        <Icon name={item.icon} size={22} filled={current} />
        {!collapsed && <span className="text-body-lg font-medium">{item.label}</span>}
      </span>
      {!collapsed && !!badge && (
        <span className="rounded-full bg-tertiary-container px-1.5 py-0.5 font-mono text-mono-sm font-semibold text-on-tertiary-container"
              aria-label={`${badge} unusually cheap`}>{badge}</span>
      )}
    </a>
  );
}

export function Sidebar({ screen, collapsed }: { screen: Screen; collapsed: boolean }) {
  const { status } = useEngine();
  const deals = useLoad(api.dealsSummary, []);
  useReloadOn(["cycle_finished", "deal_hidden", "preferences_changed"], deals.reload);
  const cheap = deals.data?.summary.unusually_cheap;

  const section = (title: string, items: NavItem[]) => (
    <nav className="flex flex-col gap-space-xs" aria-label={title}>
      {!collapsed && <div className="label-caps px-space-md py-space-xs">{title}</div>}
      {items.map((item) => (
        <NavLink key={item.screen} item={item} current={screen === item.screen} collapsed={collapsed}
                 badge={item.screen === "deals" ? cheap : undefined} />
      ))}
    </nav>
  );

  return (
    <aside className={`fixed top-16 bottom-0 left-0 z-30 flex flex-col justify-between overflow-y-auto border-r border-stroke bg-surface-container-lowest transition-[width] ${collapsed ? "w-18 p-space-sm" : "w-68 p-space-md"}`}>
      <div className="flex flex-col gap-space-lg">
        {section("Monitor", MONITOR)}
        {section("Configuration", CONFIGURATION)}
      </div>
      {status && (
        <div className={`mt-space-md flex flex-col gap-space-xs rounded-xl border border-stroke bg-surface-container-low ${collapsed ? "items-center p-space-sm" : "p-space-md"}`}>
          <div className="flex items-center justify-between gap-space-xs">
            <span className="flex items-center gap-1.5 font-mono text-mono-sm text-on-surface-variant"
                  title={ENGINE_LABELS[status.state]}>
              <StateDot state={status.state} />
              {!collapsed && ENGINE_LABELS[status.state]}
            </span>
            {!collapsed && status.app === "desktop" && (
              <button className="text-on-surface-variant transition-colors hover:text-on-surface"
                      onClick={() => api.hideWindow().catch(() => {})}
                      aria-label="Hide to the tray" title="Hide to the tray (CheapTrip keeps searching)">
                <Icon name="system_update_alt" size={16} />
              </button>
            )}
          </div>
          {!collapsed && (
            <div className="flex items-center justify-between text-caption text-outline">
              <span className="font-mono text-mono-sm">v{status.version}</span>
              <span>{status.next_search_at && status.state !== "searching" ? `Next ${clock(status.next_search_at)}` : `${status.searches_today} today`}</span>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
