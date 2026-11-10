/**
 * Base-map tile layers shared by the main map (MapView) and the field-reception map (FleetMap).
 * The chosen base map is `activeBaseMapId` in the store (picked in 🗂 שכבות).
 */
import L from "leaflet";
import type { BaseMapId } from "../store/useStore";

const TILE_DEFS: Record<BaseMapId, { url: string; opts: L.TileLayerOptions }> = {
  "esri-street": {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    opts: { attribution: "© Esri © OpenStreetMap", maxZoom: 20 },
  },
  "esri-gray": {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    opts: { attribution: "© Esri © OpenStreetMap", maxZoom: 16 },
  },
  "stadia-dark": {
    url: "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png",
    opts: { attribution: "© Stadia Maps © OpenMapTiles © OpenStreetMap", maxZoom: 20 },
  },
  "stadia-osm": {
    url: "https://tiles.stadiamaps.com/tiles/osm_bright/{z}/{x}/{y}{r}.png",
    opts: { attribution: "© Stadia Maps © OpenMapTiles © OpenStreetMap", maxZoom: 20 },
  },
  "topo": {
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    opts: { attribution: "© OpenTopoMap", maxZoom: 17 },
  },
  "satellite": {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    opts: { attribution: "© Esri World Imagery", maxZoom: 18 },
  },
};

export const BASE_MAP_IDS = Object.keys(TILE_DEFS) as BaseMapId[];

/** A new Leaflet tile layer for the base map `id` (falls back to esri-street). */
export function makeBaseLayer(id: BaseMapId | string | null | undefined): L.TileLayer {
  const def = TILE_DEFS[(id as BaseMapId)] ?? TILE_DEFS["esri-street"];
  return L.tileLayer(def.url, def.opts);
}

/** Initial view / bounds of the app's maps (Israel). */
export const ISRAEL_CENTER: L.LatLngTuple = [31.5, 35.0];
export const ISRAEL_MAX_BOUNDS: L.LatLngBoundsExpression = [[29.0, 33.5], [33.8, 36.5]];
