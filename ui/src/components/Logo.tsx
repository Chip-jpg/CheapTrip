import { useId } from "react";

/** The app's mark (packaging/icons/cheaptrip.svg): a compass star on a radar, with the deal dot. */
export function Logo({ size = 32 }: { size?: number }) {
  const gradient = useId();
  return (
    <svg viewBox="0 0 48 48" width={size} height={size} fill="none" role="img" aria-label="CheapTrip">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0067C0" />
          <stop offset="1" stopColor="#004E8C" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="10" fill={`url(#${gradient})`} />
      <circle cx="24" cy="24" r="16" stroke="rgba(255,255,255,0.25)" strokeWidth="1.5" strokeDasharray="2 3" />
      <circle cx="24" cy="24" r="10" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" />
      <path d="M24 10 L26 21 L37 23 L26 25 L24 36 L22 25 L11 23 L22 21 Z" fill="#ffffff" opacity="0.95" />
      <circle cx="33" cy="15" r="4.5" fill="#003B6F" />
      <circle cx="33" cy="15" r="3" fill="#3DD598" />
    </svg>
  );
}
