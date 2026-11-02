/** The pyGCxGC logo (simple variant: axes and spots), also `public/favicon.svg`. */

import { cn } from 'cn'

export function AppLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 249.6 247.8" className={cn('shrink-0', className)} aria-hidden>
      <rect x=".9" width="247.8" height="247.8" rx="15.7" fill="#0f1122" />
      <polyline
        points="16.6 29.6 16.6 228.9 232.7 228.9"
        fill="none"
        stroke="#fff"
        strokeWidth="8"
      />
      <polygon points="3.7 29.6 16.6 7.2 29.6 29.6" fill="#fff" />
      <polygon points="224.2 241.8 246.5 228.9 224.2 216" fill="#fff" />
      <circle cx="54.4" cy="137.9" r="21.1" fill="#10948b" />
      <circle cx="100" cy="177.7" r="10.2" fill="#e25a48" />
      <circle cx="158" cy="151.1" r="13.3" fill="#367ebd" />
      <circle cx="115.2" cy="99.2" r="13.3" fill="#fcbe35" />
      <circle cx="176" cy="57.6" r="15.2" fill="#f7933d" />
      <circle cx="224.7" cy="108.6" r="10.7" fill="#4555a0" />
    </svg>
  )
}
