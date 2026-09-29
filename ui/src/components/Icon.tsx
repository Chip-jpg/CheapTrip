/** One icon from the design's set (Material Symbols Outlined), e.g. <Icon name="radar" />. */
export function Icon({ name, size = 18, filled = false, className = "" }: {
  name: string; size?: number; filled?: boolean; className?: string;
}) {
  return (
    <span aria-hidden="true" className={`icon ${filled ? "icon-filled" : ""} ${className}`}
          style={{ fontSize: size, width: size, height: size }}>{name}</span>
  );
}
