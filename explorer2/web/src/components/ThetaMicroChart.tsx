import { useMemo, useState } from 'react'
import { formatNumber } from '../lib/format'

interface ThetaPoint {
  height: number
  time: number
  thetaMicro: number | null
}

interface ThetaMicroChartProps {
  points: ThetaPoint[]
}

interface PlotPoint {
  height: number
  time: number
  thetaMicro: number
  x: number
  y: number
}

const WIDTH = 760
const HEIGHT = 260
const PADDING = { top: 20, right: 16, bottom: 28, left: 60 }

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function formatTheta(value: number): string {
  return `${formatNumber(Math.round(value))} µnats`
}

export default function ThetaMicroChart({ points }: ThetaMicroChartProps) {
  const [hoveredHeight, setHoveredHeight] = useState<number | null>(null)

  const series = useMemo(() => {
    return [...points].sort((a, b) => a.height - b.height)
  }, [points])

  const chart = useMemo(() => {
    const plotWidth = WIDTH - PADDING.left - PADDING.right
    const plotHeight = HEIGHT - PADDING.top - PADDING.bottom

    const thetaValues = series
      .map((point) => point.thetaMicro)
      .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))

    if (series.length === 0 || thetaValues.length === 0) {
      return {
        plotPoints: [] as PlotPoint[],
        path: '',
        areaPath: '',
        minTheta: 0,
        maxTheta: 0,
        startHeight: null as number | null,
        endHeight: null as number | null
      }
    }

    const rawMin = Math.min(...thetaValues)
    const rawMax = Math.max(...thetaValues)
    const span = rawMax - rawMin
    const minTheta = rawMin - (span > 0 ? span * 0.1 : Math.max(1, rawMin * 0.05))
    const maxTheta = rawMax + (span > 0 ? span * 0.1 : Math.max(1, rawMax * 0.05))
    const safeSpan = Math.max(maxTheta - minTheta, 1)

    const plotPoints = series.flatMap((point, index) => {
      if (typeof point.thetaMicro !== 'number' || !Number.isFinite(point.thetaMicro)) return []

      const x = PADDING.left + (plotWidth * index) / Math.max(series.length - 1, 1)
      const y = PADDING.top + ((maxTheta - point.thetaMicro) / safeSpan) * plotHeight
      return [{
        height: point.height,
        time: point.time,
        thetaMicro: point.thetaMicro,
        x,
        y
      }]
    })

    const path = plotPoints
      .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
      .join(' ')

    const areaPath = plotPoints.length > 0
      ? [
          `M ${plotPoints[0].x.toFixed(2)} ${(HEIGHT - PADDING.bottom).toFixed(2)}`,
          ...plotPoints.map((point) => `L ${point.x.toFixed(2)} ${point.y.toFixed(2)}`),
          `L ${plotPoints[plotPoints.length - 1].x.toFixed(2)} ${(HEIGHT - PADDING.bottom).toFixed(2)}`,
          'Z'
        ].join(' ')
      : ''

    return {
      plotPoints,
      path,
      areaPath,
      minTheta,
      maxTheta,
      startHeight: series[0]?.height ?? null,
      endHeight: series[series.length - 1]?.height ?? null
    }
  }, [series])

  const hoveredPoint = chart.plotPoints.find((point) => point.height === hoveredHeight) ?? null

  const yTicks = useMemo(() => {
    if (!chart.plotPoints.length) return []
    const ticks = []
    for (let i = 0; i <= 3; i += 1) {
      const ratio = i / 3
      const value = chart.maxTheta - (chart.maxTheta - chart.minTheta) * ratio
      const y = PADDING.top + (HEIGHT - PADDING.top - PADDING.bottom) * ratio
      ticks.push({ value, y })
    }
    return ticks
  }, [chart.maxTheta, chart.minTheta, chart.plotPoints.length])

  const onMove = (clientX: number, rect: DOMRect) => {
    if (!chart.plotPoints.length) return
    const x = ((clientX - rect.left) / rect.width) * WIDTH
    const nearest = chart.plotPoints.reduce((best, point) => (
      Math.abs(point.x - x) < Math.abs(best.x - x) ? point : best
    ), chart.plotPoints[0])
    setHoveredHeight(nearest.height)
  }

  if (!chart.plotPoints.length) {
    return (
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-slate-100">Theta Micro Over Blocks</h3>
        <p className="mt-2 text-sm text-gray-600 dark:text-slate-400">No theta history available yet.</p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-slate-100">Theta Micro Over Blocks</h3>
        <span className="text-xs text-gray-500 dark:text-slate-400">
          #{formatNumber(chart.startHeight ?? 0)} to #{formatNumber(chart.endHeight ?? 0)}
        </span>
      </div>

      <div className="relative">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="h-64 w-full"
          role="img"
          aria-label="Theta micro over recent blocks"
          onMouseMove={(event) => onMove(event.clientX, event.currentTarget.getBoundingClientRect())}
          onMouseLeave={() => setHoveredHeight(null)}
          onTouchMove={(event) => {
            const touch = event.touches[0]
            if (!touch) return
            onMove(touch.clientX, event.currentTarget.getBoundingClientRect())
          }}
          onTouchEnd={() => setHoveredHeight(null)}
        >
          <defs>
            <linearGradient id="thetaAreaGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgb(14 165 233)" stopOpacity="0.35" />
              <stop offset="100%" stopColor="rgb(14 165 233)" stopOpacity="0.03" />
            </linearGradient>
          </defs>

          {yTicks.map((tick) => (
            <g key={tick.y}>
              <line
                x1={PADDING.left}
                y1={tick.y}
                x2={WIDTH - PADDING.right}
                y2={tick.y}
                className="stroke-gray-200 dark:stroke-night-700"
                strokeWidth="1"
              />
              <text
                x={PADDING.left - 8}
                y={tick.y + 4}
                textAnchor="end"
                className="fill-gray-500 text-[11px] dark:fill-slate-400"
              >
                {formatNumber(Math.round(tick.value))}
              </text>
            </g>
          ))}

          <path d={chart.areaPath} fill="url(#thetaAreaGradient)" />
          <path d={chart.path} fill="none" stroke="rgb(2 132 199)" strokeWidth="2.5" />

          {chart.plotPoints.map((point) => (
            <circle
              key={point.height}
              cx={point.x}
              cy={point.y}
              r={hoveredPoint?.height === point.height ? 4 : 2.5}
              fill={hoveredPoint?.height === point.height ? 'rgb(2 132 199)' : 'rgb(14 116 144)'}
            />
          ))}

          {hoveredPoint && (
            <line
              x1={hoveredPoint.x}
              y1={PADDING.top}
              x2={hoveredPoint.x}
              y2={HEIGHT - PADDING.bottom}
              stroke="rgb(2 132 199 / 0.45)"
              strokeWidth="1"
            />
          )}
        </svg>

        {hoveredPoint && (
          <div
            className="pointer-events-none absolute z-10 rounded-lg border border-day-200 bg-white px-3 py-2 text-xs shadow-md dark:border-night-700 dark:bg-night-900"
            style={{
              left: `${clamp((hoveredPoint.x / WIDTH) * 100, 8, 92)}%`,
              top: `${clamp((hoveredPoint.y / HEIGHT) * 100 - 14, 2, 82)}%`,
              transform: 'translate(-50%, -100%)'
            }}
          >
            <div className="font-semibold text-gray-900 dark:text-slate-100">Block #{formatNumber(hoveredPoint.height)}</div>
            <div className="mt-0.5 text-gray-600 dark:text-slate-400">{formatTheta(hoveredPoint.thetaMicro)}</div>
          </div>
        )}
      </div>
    </div>
  )
}
