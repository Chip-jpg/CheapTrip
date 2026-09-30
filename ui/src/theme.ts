/**
 * Light and dark: <html data-theme> picks the colour set (theme.css). The choice
 * lives in Settings → App ("system" follows Windows) and applies at once.
 *
 * The text size too (B47): <html data-text-size> sets the root font size, and the
 * whole app is sized in rem, so everything grows with it. Ctrl + / Ctrl − / Ctrl 0
 * change it from anywhere.
 */
import { createContext, createElement, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type TextSize, type ThemeChoice } from "./api";

export type Theme = "light" | "dark";
export const TEXT_SIZES: TextSize[] = ["standard", "large", "largest"];

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

export function applyTextSize(size: TextSize): void {
  if (size === "standard") delete document.documentElement.dataset.textSize;
  else document.documentElement.dataset.textSize = size;
}

function markedTextSize(): TextSize {
  const marked = document.documentElement.dataset.textSize;
  return TEXT_SIZES.includes(marked as TextSize) ? (marked as TextSize) : "standard";
}

/** The size Ctrl + / Ctrl − / Ctrl 0 asks for, or undefined when the key isn't one of them. */
export function textSizeForKey(event: Pick<KeyboardEvent, "ctrlKey" | "metaKey" | "altKey" | "key">, current: TextSize): TextSize | undefined {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return undefined;
  const at = TEXT_SIZES.indexOf(current);
  if (event.key === "=" || event.key === "+") return TEXT_SIZES[Math.min(TEXT_SIZES.length - 1, at + 1)];
  if (event.key === "-" || event.key === "_") return TEXT_SIZES[Math.max(0, at - 1)];
  if (event.key === "0") return "standard";
  return undefined;
}

interface ThemeState {
  choice: ThemeChoice;
  theme: Theme;
  /** Apply a choice now and save it (Settings → App). */
  choose: (choice: ThemeChoice) => void;
  textSize: TextSize;
  /** Apply a text size now and save it (Settings → App, Ctrl + / −). */
  chooseTextSize: (size: TextSize) => void;
}

const ThemeContext = createContext<ThemeState>({
  choice: "system", theme: "dark", choose: () => {}, textSize: "standard", chooseTextSize: () => {},
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoice] = useState<ThemeChoice>("system");
  // As the page opened: the engine marks a chosen theme on <html>, else the page guessed from Windows
  const [theme, setTheme] = useState<Theme>(() => {
    const marked = document.documentElement.dataset.theme;
    return marked === "light" || marked === "dark" ? marked : applyTheme("system");
  });
  const [textSize, setTextSize] = useState<TextSize>(markedTextSize);

  useEffect(() => {
    api.settings().then((s) => {
      setChoice(s.app.theme);
      setTheme(applyTheme(s.app.theme));
      if (s.app.text_size) {
        setTextSize(s.app.text_size);
        applyTextSize(s.app.text_size);
      }
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

  const chooseTextSize = useCallback((next: TextSize) => {
    setTextSize(next);
    applyTextSize(next);
    api.saveSettings({ app: { text_size: next } }).catch(() => {});
  }, []);

  useEffect(() => {  // Ctrl + / Ctrl − / Ctrl 0: bigger, smaller, standard (the window's own zoom keys are off)
    const onKey = (event: KeyboardEvent) => {
      const next = textSizeForKey(event, textSize);
      if (next === undefined) return;
      event.preventDefault();
      if (next !== textSize) chooseTextSize(next);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [textSize, chooseTextSize]);

  return createElement(ThemeContext.Provider, { value: { choice, theme, choose, textSize, chooseTextSize } }, children);
}

export function useTheme(): ThemeState {
  return useContext(ThemeContext);
}
