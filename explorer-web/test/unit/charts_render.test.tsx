import { describe, it, expect, beforeAll, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import React from 'react'

// Import chart modules (we'll pick default or a sensible named export at runtime)
import * as TimeSeriesMod from '../../src/components/charts/TimeSeriesChart'
import * as StackedBarMod from '../../src/components/charts/StackedBarChart'
import * as DonutMod from '../../src/components/charts/DonutChart'
import * as GaugeMod from '../../src/components/charts/GaugeChart'
import * as SparklineMod from '../../src/components/charts/Sparkline'
import * as LegendMod from '../../src/components/charts/Legend'

type AnyObj = Record<string, any>

// --- Test environment shims (canvas, ResizeObserver, getBBox) ---

beforeAll(() => {
  // ResizeObserver (used by many chart libs)
  ;(globalThis as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }

  // getBBox for SVG text measuring
  if (!(globalThis as any).SVGElement) {
    ;(globalThis as any).SVGElement = class {} as any
  }
  ;(globalThis as any).SVGElement.prototype.getBBox = vi.fn(() => ({
    x: 0, y: 0, width: 100, height: 20
  }))

  // Canvas 2D context stub
  ;(globalThis as any).HTMLCanvasElement.prototype.getContext = vi.fn(() => {
    return {
      // minimal set used by typical drawing code
      measureText: (t: string) => ({ width: t?.length ?? 10 }),
      fillRect: () => {},
      clearRect: () => {},
      getImageData: () => ({ data: new Uint8ClampedArray(0) }),
      putImageData: () => {},
      createImageData: () => new ImageData(1, 1),
      setTransform: () => {},
      drawImage: () => {},
      save: () => {},
      restore: () => {},
      beginPath: () => {},
      closePath: () => {},
      moveTo: () => {},
      lineTo: () => {},
      stroke: () => {},
      strokeRect: () => {},
      rect: () => {},
      fill: () => {},
      translate: () => {},
      scale: () => {},
      rotate: () => {},
      arc: () => {},
      fillText: () => {},
      strokeText: () => {},
      // state
      canvas: {},
    } as any
  })
})

// --- Helpers ---

function pickComponent(mod: AnyObj, ...candidates: string[]) {
  if (typeof mod?.default === 'function') return mod.default
  for (const name of candidates) {
    if (typeof mod?.[name] === 'function') return mod[name]
  }
  throw new Error('No renderable component export found')
}

function smokeRender(Comp: React.ComponentType<any>, props: AnyObj, label: string) {
  const { container, unmount } = render(<Comp {...(props as any)} />)
  // Basic existence checks
  expect(container.firstElementChild, `${label} should render a root element`).toBeTruthy()
  // Likely chart roots (svg/canvas) or a wrapping div
  const el = container.querySelector('svg,canvas,div')
  expect(el, `${label} should render visual element`).toBeTruthy()
  unmount()
  cleanup()
}

// --- Sample datasets (loose typed via any so tests are resilient to prop changes) ---

const tsDataXY = Array.from({ length: 16 }).map((_, i) => ({
  x: Date.now() - (16 - i) * 60_000,
  y: Math.round(50 + 30 * Math.sin(i / 2)),
}))
const tsDataTV = tsDataXY.map(p => ({ t: p.x, v: p.y }))

const stackedCats = ['A', 'B', 'C', 'D']
const stackedSeries = [
  { name: 'S1', values: [3, 5, 2, 6] },
  { name: 'S2', values: [4, 2, 3, 1] },
  { name: 'S3', values: [1, 3, 4, 2] },
]

const donutSlices = [
  { label: 'Alpha', value: 40 },
  { label: 'Beta', value: 25 },
  { label: 'Gamma', value: 20 },
  { label: 'Other', value: 15 },
]

const gaugeSample = { value: 62, min: 0, max: 100, target: 75 }

const sparkValues = tsDataXY.map(p => p.y)

const legendItems = [
  { label: 'Alpha', color: '#6b5bfd' },
  { label: 'Beta', color: '#00c2a8' },
  { label: 'Gamma', color: '#ff8a00' },
]

// --- Tests ---

describe('Chart components render with sample data', () => {
  it('TimeSeriesChart renders (x,y dataset)', () => {
    const Comp = pickComponent(TimeSeriesMod, 'TimeSeriesChart')
    // Try a handful of prop names that our chart might accept
    const props: AnyObj = {
      data: tsDataXY,
      series: [{ id: 'y', data: tsDataXY }],
      width: 640,
      height: 240,
      xKey: 'x',
      yKey: 'y',
    }
    smokeRender(Comp, props, 'TimeSeriesChart(xy)')
  })

  it('TimeSeriesChart renders (t,v dataset)', () => {
    const Comp = pickComponent(TimeSeriesMod, 'TimeSeriesChart')
    const props: AnyObj = {
      data: tsDataTV,
      series: [{ id: 'v', data: tsDataTV }],
      width: 640,
      height: 240,
      xKey: 't',
      yKey: 'v',
    }
    smokeRender(Comp, props, 'TimeSeriesChart(tv)')
  })

  it('StackedBarChart renders', () => {
    const Comp = pickComponent(StackedBarMod, 'StackedBarChart')
    const props: AnyObj = {
      categories: stackedCats,
      series: stackedSeries,
      width: 640,
      height: 280,
      // Provide a fallback data shape too
      data: stackedCats.map((c, i) => ({
        category: c,
        S1: stackedSeries[0].values[i],
        S2: stackedSeries[1].values[i],
        S3: stackedSeries[2].values[i],
      })),
    }
    smokeRender(Comp, props, 'StackedBarChart')
  })

  it('DonutChart renders', () => {
    const Comp = pickComponent(DonutMod, 'DonutChart')
    const props: AnyObj = {
      data: donutSlices,
      slices: donutSlices,
      width: 280,
      height: 280,
      innerRadius: 60,
      outerRadius: 120,
    }
    smokeRender(Comp, props, 'DonutChart')
  })

  it('GaugeChart renders', () => {
    const Comp = pickComponent(GaugeMod, 'GaugeChart')
    const props: AnyObj = {
      ...gaugeSample,
      width: 360,
      height: 200,
      label: 'Utilization',
      units: '%',
    }
    smokeRender(Comp, props, 'GaugeChart')
  })

  it('Sparkline renders', () => {
    const Comp = pickComponent(SparklineMod, 'Sparkline')
    const props: AnyObj = {
      values: sparkValues,
      width: 200,
      height: 40,
    }
    smokeRender(Comp, props, 'Sparkline')
  })

  it('Legend renders', () => {
    const Comp = pickComponent(LegendMod, 'Legend')
    const props: AnyObj = {
      items: legendItems,
      // In case the component expects a different prop name:
      data: legendItems,
      orientation: 'horizontal',
    }
    smokeRender(Comp, props, 'Legend')
  })
})
