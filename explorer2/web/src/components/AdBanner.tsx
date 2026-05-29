// A-Ads adaptive banner for explorer.animica.org.
//
// Design intent mirrors the homepage variant on animica.org:
//  - Renders ABOVE the sticky site header so it scrolls away with the
//    page (intentionally non-sticky — pinned ads are the single most
//    common UX complaint we want to avoid).
//  - Explicit "Sponsored" disclosure + a "Support the Explorer" CTA
//    that honestly states what a click does, without misleading
//    pointing-arrows or excessive urgency.
//  - Dismissible per session via sessionStorage so navigating between
//    Explorer routes doesn't re-show the ad after the user closes it.
//  - Theme-aware via the existing day-*/night-* token classes; no
//    new tokens required.

import { useEffect, useState } from 'react'

interface AdBannerProps {
  /** A-Ads unit id. Defaults to 2439597 (Explorer adaptive unit). */
  unitId?: string
}

const STORAGE_KEY = 'animica.explorer.adBanner.dismissed.v1'

export default function AdBanner({ unitId = '2439597' }: AdBannerProps) {
  // Default to hidden during the SSR/initial-render pass so dismissal
  // doesn't flicker on hard reloads. Reveal on the first effect tick
  // once we've consulted sessionStorage. Safe across SSR-free Vite +
  // SPA flows alike.
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    try {
      const dismissed = window.sessionStorage.getItem(STORAGE_KEY) === '1'
      setVisible(!dismissed)
    } catch {
      // sessionStorage can throw on hardened browsers (Safari private
      // mode historically); default to showing so the ad still serves
      // rather than silently failing.
      setVisible(true)
    }
  }, [])

  if (!visible) return null

  const onDismiss = () => {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, '1')
    } catch {
      // Best-effort persist; remove the node either way.
    }
    setVisible(false)
  }

  const iframeSrc = `//acceptable.a-ads.com/${unitId}/?size=Adaptive`

  return (
    <aside
      aria-label="Advertisement"
      className="
        relative z-[5] w-full
        border-b border-day-200 bg-day-100/70 backdrop-blur-sm
        dark:border-night-800 dark:bg-night-900/70
      "
    >
      <div className="mx-auto flex w-full max-w-[880px] items-center gap-3 px-3 py-2 sm:px-4">
        <div className="flex shrink-0 min-w-0 flex-col gap-[2px] max-w-[240px]">
          <span
            className="
              text-[10px] tracking-[0.16em] uppercase
              font-mono
              text-gray-500 dark:text-slate-500
            "
          >
            Sponsored
          </span>
          <span className="text-[13px] leading-tight text-gray-700 dark:text-slate-300">
            <strong className="font-semibold text-gray-900 dark:text-slate-100">
              Support the Explorer
            </strong>
            <span className="text-gray-500 dark:text-slate-400">
              {' '}
              — a click on the ad keeps it free + open-source.
            </span>
          </span>
        </div>
        <div
          className="
            flex h-[90px] min-w-0 flex-1 items-center justify-center
            max-w-[728px]
          "
        >
          {/* BEGIN AADS AD UNIT */}
          <iframe
            data-aa={unitId}
            src={iframeSrc}
            title="Advertisement"
            loading="lazy"
            referrerPolicy="no-referrer-when-downgrade"
            scrolling="no"
            sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-same-origin"
            className="block h-full w-full overflow-hidden border-0 bg-transparent"
            style={{ colorScheme: 'normal' }}
          />
          {/* END AADS AD UNIT */}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss advertisement"
          className="
            inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full
            border border-day-300 bg-transparent text-gray-500
            transition-colors hover:border-day-400 hover:bg-day-200/60 hover:text-gray-900
            dark:border-night-700 dark:text-slate-400 dark:hover:border-night-500 dark:hover:bg-night-800/60 dark:hover:text-slate-100
          "
        >
          <svg
            viewBox="0 0 14 14"
            aria-hidden="true"
            focusable="false"
            className="h-3 w-3"
          >
            <path
              d="M2 2 L12 12 M12 2 L2 12"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
      <style>{`
        @media (max-width: 640px) {
          aside[aria-label="Advertisement"] > div > div:first-child {
            max-width: 110px;
          }
          aside[aria-label="Advertisement"] > div > div:first-child > span:first-child,
          aside[aria-label="Advertisement"] > div > div:first-child > span:last-child > span:last-child {
            display: none;
          }
          aside[aria-label="Advertisement"] > div > div:nth-child(2) {
            height: 60px;
          }
        }
      `}</style>
    </aside>
  )
}
