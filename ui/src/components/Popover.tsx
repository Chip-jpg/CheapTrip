/**
 * A flyout that opens from a button (the design's acrylic menus): closes on a
 * click outside, on Escape, and when one of its items is chosen.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";

export function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return { open, setOpen, toggle: () => setOpen((o) => !o), ref };
}

/** `button` renders the trigger (given open and toggle); the children are the flyout's content. */
export function Popover({ button, children, align = "left", className = "w-64" }: {
  button: (open: boolean, toggle: () => void) => ReactNode;
  children: (close: () => void) => ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  const { open, setOpen, toggle, ref } = usePopover();
  return (
    <div className="relative" ref={ref}>
      {button(open, toggle)}
      {open && (
        <div role="menu" className={`flyout absolute z-50 mt-space-xs p-space-xs ${align === "right" ? "right-0" : "left-0"} ${className}`}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

/** A heading inside a flyout ("PAUSE ALERTS FOR"). */
export function MenuHeading({ children }: { children: ReactNode }) {
  return <div className="label-caps px-space-md py-space-xs">{children}</div>;
}

export function MenuItem({ children, onSelect, className = "" }: {
  children: ReactNode; onSelect: () => void; className?: string;
}) {
  return (
    <button role="menuitem" onClick={onSelect}
            className={`flex w-full items-center gap-space-sm rounded-lg px-space-md py-1.5 text-left text-body text-on-surface hover:bg-surface-container-highest ${className}`}>
      {children}
    </button>
  );
}
