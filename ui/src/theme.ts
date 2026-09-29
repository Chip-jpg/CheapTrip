/**
 * Light and dark: <html data-theme> picks the colour set (theme.css). The choice
 * lives in Settings → App ("system" follows Windows) and applies at once.
 */
import { createContext, createElement, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type ThemeChoice } from "./api";

export type Theme = "light" | "dark";

function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function resolveTheme(choice: ThemeChoice): Theme {
  return choice === "system" ? systemTheme() : choice;
}

export function applyTheme(choice: ThemeChoice): Theme {
  const theme = resolveTheme(choice);
  document.documentElement.dataset.theme = theme;
  return theme;
}

interface ThemeState {
  choice: ThemeChoice;
  theme: Theme;
  /** Apply a choice now and save it (Settings → App). */
  choose: (choice: ThemeChoice) => void;
}

const ThemeContext = createContext<ThemeState>({ choice: "system", theme: "dark", choose: () => {} });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoice] = useState<ThemeChoice>("system");
  const [theme, setTheme] = useState<Theme>(() => applyTheme("system"));

  useEffect(() => {
    api.settings().then((s) => {
      setChoice(s.app.theme);
      setTheme(applyTheme(s.app.theme));
    }).catch(() => {});
  }, []);

  useEffect(() => {  // "system" follows Windows switching between light and dark
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media || choice !== "system") return;
    const follow = () => setTheme(applyTheme("system"));
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, [choice]);

  const choose = useCallback((next: ThemeChoice) => {
    setChoice(next);
    setTheme(applyTheme(next));
    api.saveSettings({ app: { theme: next } }).catch(() => {});
  }, []);

  return createElement(ThemeContext.Provider, { value: { choice, theme, choose } }, children);
}

export function useTheme(): ThemeState {
  return useContext(ThemeContext);
}
