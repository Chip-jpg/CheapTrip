/**
 * Short confirmations at the bottom right ("Berlin muted · Undo"), gone after a
 * few seconds. Screen readers hear them through a polite live region.
 */
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

interface Toast {
  id: number;
  text: string;
  tone: "ok" | "error";
  action?: { label: string; run: () => void };
}

type Show = (text: string, options?: { tone?: Toast["tone"]; action?: Toast["action"] }) => void;

const ToastContext = createContext<Show>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(0);
  const show = useCallback<Show>((text, options = {}) => {
    const id = ++next.current;
    setToasts((list) => [...list.slice(-2), { id, text, tone: options.tone ?? "ok", action: options.action }]);
    window.setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), 6000);
  }, []);
  const dismiss = (id: number) => setToasts((list) => list.filter((t) => t.id !== id));
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed right-space-lg bottom-space-lg z-[60] flex flex-col items-end gap-space-sm">
        {toasts.map((toast) => (
          <div key={toast.id} role="status"
               className="flyout pointer-events-auto flex items-center gap-space-md px-space-md py-space-sm text-body text-on-surface">
            <Icon name={toast.tone === "error" ? "error" : "check_circle"} size={18}
                  className={toast.tone === "error" ? "text-error" : "text-secondary"} />
            <span>{toast.text}</span>
            {toast.action && (
              <button className="font-semibold text-primary hover:underline"
                      onClick={() => { toast.action!.run(); dismiss(toast.id); }}>{toast.action.label}</button>
            )}
            <button className="btn-icon h-6 w-6" aria-label="Dismiss" onClick={() => dismiss(toast.id)}>
              <Icon name="close" size={16} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): Show {
  return useContext(ToastContext);
}
