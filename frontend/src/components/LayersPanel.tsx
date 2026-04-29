import { useState } from "react";
import { useStore } from "../store/useStore";
import type { BaseMapId, MapLayer } from "../store/useStore";
import styles from "./LayersPanel.module.css";

// ── Base-map catalogue ────────────────────────────────────────────────────────
const BASE_MAPS: { id: BaseMapId; label: string; icon: string; desc: string }[] = [
  { id: "esri-street",  label: "מפה נקייה",          icon: "🗺️", desc: "Esri Street — ברירת מחדל" },
  { id: "esri-gray",    label: "מפה אפורה",           icon: "⬜", desc: "Esri Gray — נייטרלי" },
  { id: "stadia-dark",  label: "כהה / לילה",          icon: "🌙", desc: "Stadia Dark — טקטי" },
  { id: "stadia-osm",   label: "רחובות מפורטים",      icon: "🏙️", desc: "Stadia OSM Bright" },
  { id: "topo",         label: "טופוגרפיה",           icon: "⛰️", desc: "OpenTopoMap — קווי גובה" },
  { id: "satellite",    label: "לוויין",              icon: "🛰️", desc: "Esri World Imagery" },
];

const LAYER_ICONS: Record<string, string> = {
  cellular: "📡",
  dem:      "🏔️",
  coverage: "🔲",
};

const LAYER_DESCS: Record<string, string> = {
  cellular: "אתרי סלולר מ-OpenStreetMap",
  dem:      "שכבת גבהים דינמית (DEM)",
};

export default function LayersPanel() {
  const {
    activeBaseMapId, setActiveBaseMapId,
    mapLayers, toggleMapLayer, removeMapLayer, renameMapLayer,
  } = useStore();

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName,  setEditName]  = useState("");

  const systemLayers   = mapLayers.filter((l) => l.type !== "coverage");
  const coverageLayers = mapLayers.filter((l) => l.type === "coverage")
    .slice().reverse(); // newest first

  function startRename(layer: MapLayer) {
    setEditingId(layer.id);
    setEditName(layer.name);
  }

  function commitRename(id: string) {
    if (editName.trim()) renameMapLayer(id, editName.trim());
    setEditingId(null);
  }

  return (
    <div className={styles.root}>

      {/* ── Base map ──────────────────────────────────────────────────────── */}
      <div className={styles.sectionHeader}>מפת בסיס</div>
      <div className={styles.baseMapGrid}>
        {BASE_MAPS.map((bm) => (
          <button
            key={bm.id}
            className={`${styles.baseMapBtn} ${activeBaseMapId === bm.id ? styles.activeBase : ""}`}
            onClick={() => setActiveBaseMapId(bm.id)}
            title={bm.desc}
          >
            <span className={styles.baseMapIcon}>{bm.icon}</span>
            <span className={styles.baseMapLabel}>{bm.label}</span>
            {activeBaseMapId === bm.id && <span className={styles.baseCheck}>✓</span>}
          </button>
        ))}
      </div>

      {/* ── System layers ─────────────────────────────────────────────────── */}
      <div className={styles.sectionHeader} style={{ marginTop: 18 }}>שכבות מידע</div>
      {systemLayers.map((layer) => (
        <div key={layer.id} className={styles.layerRow}>
          <button
            className={`${styles.visBtn} ${layer.visible ? styles.visBtnOn : ""}`}
            onClick={() => toggleMapLayer(layer.id)}
            title={layer.visible ? "הסתר שכבה" : "הצג שכבה"}
          >
            {layer.visible ? "👁" : "🙈"}
          </button>
          <span className={styles.layerIcon}>{LAYER_ICONS[layer.type] ?? "📌"}</span>
          <div className={styles.layerInfo}>
            <div className={styles.layerName}>{layer.name}</div>
            <div className={styles.layerDesc}>{LAYER_DESCS[layer.id] ?? ""}</div>
          </div>
        </div>
      ))}

      {/* ── Coverage layers ───────────────────────────────────────────────── */}
      <div className={styles.sectionHeader} style={{ marginTop: 18 }}>
        ניתוחי כיסוי
        {coverageLayers.length > 0 && (
          <span className={styles.layerCount}>{coverageLayers.length}</span>
        )}
      </div>

      {coverageLayers.length === 0 ? (
        <div className={styles.emptyHint}>
          הרץ ניתוח כיסוי — כל ניתוח יישמר כאן כשכבה שניתן להפעיל/לכבות
        </div>
      ) : (
        coverageLayers.map((layer) => (
          <div key={layer.id} className={`${styles.layerRow} ${styles.coverageRow}`}>
            <button
              className={`${styles.visBtn} ${layer.visible ? styles.visBtnOn : ""}`}
              onClick={() => toggleMapLayer(layer.id)}
              title={layer.visible ? "הסתר כיסוי" : "הצג כיסוי"}
            >
              {layer.visible ? "👁" : "🙈"}
            </button>

            <div className={styles.layerInfo} style={{ flex: 1 }}>
              {editingId === layer.id ? (
                <input
                  className={styles.renameInput}
                  value={editName}
                  autoFocus
                  onChange={(e) => setEditName(e.target.value)}
                  onBlur={() => commitRename(layer.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(layer.id);
                    if (e.key === "Escape") setEditingId(null);
                  }}
                />
              ) : (
                <div
                  className={styles.layerName}
                  onClick={() => startRename(layer)}
                  title="לחץ לשינוי שם"
                >
                  {layer.name}
                  <span className={styles.editHint}>✎</span>
                </div>
              )}
              <div className={styles.layerMeta}>
                <span className={`${styles.modeTag} ${layer.mode === "DSM" ? styles.dsm : styles.dtm}`}>
                  {layer.mode ?? "DTM"}
                </span>
                {layer.coveredPct !== undefined && (
                  <span className={styles.pctTag}>{layer.coveredPct}% כיסוי</span>
                )}
                {layer.createdAt && (
                  <span className={styles.dateTag}>
                    {new Date(layer.createdAt).toLocaleDateString("he-IL", {
                      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
                    })}
                  </span>
                )}
              </div>
            </div>

            <button
              className={styles.deleteBtn}
              onClick={() => removeMapLayer(layer.id)}
              title="מחק שכבה"
            >✕</button>
          </div>
        ))
      )}
    </div>
  );
}
