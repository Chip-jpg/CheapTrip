/**
 * One icon from the design's set (Material Symbols Outlined), e.g. <Icon name="radar" />.
 * `size` is the design's size in px; icons draw an eighth larger, in rem, so they keep
 * pace with the text (and the text size setting).
 */
export function Icon({ name, size = 18, filled = false, className = "" }: {
  name: string; size?: number; filled?: boolean; className?: string;
}) {
  const rem = `${(size * 1.125) / 16}rem`;
  return (
    <span aria-hidden="true" className={`icon ${filled ? "icon-filled" : ""} ${className}`}
          style={{ fontSize: rem, width: rem, height: rem }}>{name}</span>
  );
}
