/**
 * Leaflet layer that draws the field-reception heat grid (GET /api/field/heatmap) on one canvas:
 * a semi-transparent rectangle per grid cell in the tier colour and — when a cell is large
 * enough on screen — the 0–4 bars glyph inside it, so the tier is never encoded by colour
 * alone (contract v2: "תאי גריד ... חייבים גליף פסים"). Hovering shows a tooltip with the
 * metric value, tier label, sample count and the metric's name.
 *
 * Grid convention (server): cell (gx, gy) spans south = gy·dlat … (gy+1)·dlat, west = gx·dlon … (gx+1)·dlon.
 */
import L from "leaflet";
import type { HeatCell, HeatMetric } from "../services/api";
import { tierDef, tierOfDbm, TIER_MEASURE_HE } from "./fieldTiers";
import { fmtDbm, fmtInt, fmtNum } from "./fieldFormat";

export interface HeatGridData {
  cells: HeatCell[];
  cellDeg: [number, number];
  metric: HeatMetric;
  /** e.g. "RSRP LTE" / "SS-RSRP NR" */
  measureLabel: string;
}

const OOS_COLOR = "255,59,92";

/** Tier shown for a cell: from the displayed dBm value for p50/p5, else the server's cell tier. */
export function cellTier(c: HeatCell, metric: HeatMetric): number | null {
  if (metric === "p50" || metric === "p5") return tierOfDbm(c.value);
  return c.tier;
}

export function cellTooltipHtml(c: HeatCell, d: HeatGridData): string {
  const t = tierDef(cellTier(c, d.metric));
  const metricName = d.metric === "p50" ? "P50" : d.metric === "p5" ? "P5" : d.metric === "oos_pct" ? "% זמן ללא שירות"
    : d.metric === "n" ? "דגימות" : "רמה";
  const val = d.metric === "p50" || d.metric === "p5" ? fmtDbm(c.value)
    : d.metric === "oos_pct" ? `${fmtNum(c.value, 1)}%` : fmtInt(c.value);
  const devs = c.n_devices != null ? ` · ${c.n_devices} מכשירים` : "";
  return `<div dir="rtl" style="font-size:11px;line-height:1.5">`
    + `<b>${d.measureLabel} · ${metricName}:</b> <bdi dir="ltr">${val}</bdi><br>`
    + `רמה: <b>${t.label_he}</b> (${TIER_MEASURE_HE})<br>`
    + `<bdi dir="ltr">n=${fmtInt(c.n)}</bdi>${devs}</div>`;
}

export class HeatGridLayer extends L.Layer {
  private data: HeatGridData;
  private canvas: HTMLCanvasElement | null = null;
  private index = new Map<string, HeatCell>();
  private tooltip: L.Tooltip | null = null;
  private opacity: number;

  constructor(data: HeatGridData, opts: { opacity?: number; pane?: string } = {}) {
    super({ pane: opts.pane ?? "overlayPane" });
    this.data = data;
    this.opacity = opts.opacity ?? 0.45;
    this.reindex();
  }

  setData(data: HeatGridData) {
    this.data = data;
    this.reindex();
    this.redraw();
  }

  private reindex() {
    this.index.clear();
    for (const c of this.data.cells) this.index.set(`${c.gx}:${c.gy}`, c);
  }

  onAdd(map: L.Map): this {
    this.canvas = L.DomUtil.create("canvas", "field-heat-canvas") as HTMLCanvasElement;
    this.canvas.style.pointerEvents = "none";
    this.canvas.setAttribute("data-testid", "field-heat-canvas");
    this.getPane()!.appendChild(this.canvas);
    map.on("moveend zoomend resize viewreset", this.redraw, this);
    map.on("zoomstart", this.hide, this);
    map.on("mousemove", this.onMove, this);
    map.on("mouseout", this.hideTip, this);
    this.redraw();
    return this;
  }

  onRemove(map: L.Map): this {
    map.off("moveend zoomend resize viewreset", this.redraw, this);
    map.off("zoomstart", this.hide, this);
    map.off("mousemove", this.onMove, this);
    map.off("mouseout", this.hideTip, this);
    this.hideTip();
    this.canvas?.remove();
    this.canvas = null;
    return this;
  }

  private hide() { if (this.canvas) this.canvas.style.visibility = "hidden"; }

  private hideTip() {
    if (this.tooltip && this._map) this._map.closeTooltip(this.tooltip);
    this.tooltip = null;
  }

  private onMove(e: L.LeafletMouseEvent) {
    const [dlat, dlon] = this.data.cellDeg;
    const c = this.index.get(`${Math.floor(e.latlng.lng / dlon)}:${Math.floor(e.latlng.lat / dlat)}`);
    if (!c || !this._map) { this.hideTip(); return; }
    const html = cellTooltipHtml(c, this.data);
    if (!this.tooltip) {
      this.tooltip = L.tooltip({ direction: "top", offset: [0, -6], className: "field-heat-tip" });
    }
    this.tooltip.setLatLng(e.latlng).setContent(html);
    if (!this._map.hasLayer(this.tooltip)) this._map.openTooltip(this.tooltip);
  }

  redraw() {
    const map = this._map;
    const canvas = this.canvas;
    // A layer whose map was removed (no map pane any more) must not draw
    if (!map || !canvas || !(map as unknown as { _mapPane?: HTMLElement })._mapPane) return;
    const size = map.getSize();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = size.x * ratio;
    canvas.height = size.y * ratio;
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;
    canvas.style.visibility = "visible";
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);

    const [dlat, dlon] = this.data.cellDeg;
    const bounds = map.getBounds().pad(0.05);
    for (const c of this.data.cells) {
      const s = c.gy * dlat, w = c.gx * dlon;
      if (s > bounds.getNorth() || s + dlat < bounds.getSouth() || w > bounds.getEast() || w + dlon < bounds.getWest()) continue;
      const p1 = map.latLngToContainerPoint([s + dlat, w]);
      const p2 = map.latLngToContainerPoint([s, w + dlon]);
      const x = Math.round(p1.x), y = Math.round(p1.y);
      const wPx = Math.max(1, Math.round(p2.x) - x), hPx = Math.max(1, Math.round(p2.y) - y);
      const tier = cellTier(c, this.data.metric);
      const def = tierDef(tier);
      if (this.data.metric === "oos_pct") {
        const a = Math.min(0.85, 0.12 + ((c.value ?? 0) / 100) * 0.75);
        ctx.fillStyle = `rgba(${OOS_COLOR},${a})`;
      } else {
        ctx.globalAlpha = this.opacity;
        ctx.fillStyle = def.color;
      }
      ctx.fillRect(x, y, wPx, hPx);
      ctx.globalAlpha = 1;
      if (wPx >= 6) {
        ctx.strokeStyle = "rgba(10,14,20,0.55)";
        ctx.lineWidth = 1;
        ctx.strokeRect(x + 0.5, y + 0.5, wPx - 1, hPx - 1);
      }
      // Bars glyph when the cell is big enough to read it (never colour alone)
      if (wPx >= 18 && hPx >= 14 && this.data.metric !== "oos_pct") {
        drawBars(ctx, x + wPx / 2 - 7, y + hPx / 2 - 5.5, def.bars, tier == null);
      }
    }
  }
}

/** 14×11 px bars glyph on a canvas (same geometry as fieldTiers.barsSvg). */
export function drawBars(ctx: CanvasRenderingContext2D, x: number, y: number, filled: number, empty = false) {
  for (let i = 0; i < 4; i++) {
    const bh = 3 + i * 2.6;
    const bx = x + 0.5 + i * 3.5;
    const by = y + 11 - bh;
    if (!empty && i < filled) {
      ctx.fillStyle = "rgba(10,14,20,0.9)";
      ctx.fillRect(bx, by, 2.6, bh);
    } else {
      ctx.strokeStyle = "rgba(10,14,20,0.75)";
      ctx.lineWidth = 0.9;
      ctx.strokeRect(bx, by, 2.6, bh);
    }
  }
}

/** Heat-map resolution for a zoom level (server levels 100 m / 500 m / 2.5 km; ≤ 20K cells). */
export function heatLevelForZoom(z: number): 100 | 500 | 2500 {
  if (z >= 12) return 100;
  if (z >= 9) return 500;
  return 2500;
}

export const LEVEL_TEXT_HE: Record<number, string> = { 100: "תא 100 מ׳", 500: "תא 500 מ׳", 2500: "תא 2.5 ק״מ" };

/** bbox param "sw_lat,sw_lon,ne_lat,ne_lon" for the server, clamped to valid ranges. */
export function bboxParam(b: L.LatLngBounds): string {
  const s = Math.max(-89.9, b.getSouth()), w = Math.max(-179.9, b.getWest());
  const n = Math.min(89.9, b.getNorth()), e = Math.min(179.9, b.getEast());
  return [s, w, n, e].map((v) => v.toFixed(4)).join(",");
}
