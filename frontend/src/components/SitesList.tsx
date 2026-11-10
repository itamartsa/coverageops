import { useMutation, useQueryClient } from "@tanstack/react-query";
import { sitesApi } from "../services/api";
import { useStore } from "../store/useStore";
import { notify } from "./Notification";
import { canWrite } from "../lib/roles";
import styles from "./SitesList.module.css";

const FREQ_COLORS: Record<number, string> = {
  700: "#ff6b35", 850: "#ffb347", 900:  "#00ff88",
  1800: "#00d4ff", 2100: "#a78bfa", 2600: "#f472b6",
  3500: "#f59e0b",
};

export default function SitesList() {
  const { sites, activeSiteId, setActiveSite, removeSite, setEditingSite,
          setDrawMode, setActiveTab, setBbox, setCoverageResult,
          setSelectedSiteIds, user } = useStore();
  const writable = canWrite(user?.role);
  const qc = useQueryClient();

  const deleteMut = useMutation({
    mutationFn: (id: number) => sitesApi.delete(id),
    onSuccess: (_, id) => {
      removeSite(id);
      setCoverageResult(null);
      // The backend deleted the site's results too → drop their coverage layers from the map
      const st = useStore.getState();
      const gone = st.mapLayers.filter((l) => l.type === "coverage" && l.siteId === id);
      gone.forEach((l) => st.removeMapLayer(l.id));
      st.forgetLayerNames(gone.map((l) => l.id));
      setSelectedSiteIds(useStore.getState().selectedSiteIds.filter((x) => x !== id));
      qc.invalidateQueries({ queryKey: ["sites"] });
      notify("אתר נמחק");
    },
  });

  if (!sites.length) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyIcon}>📡</div>
        אין אתרי קשר מוגדרים{writable && <><br />עבור ל"הקמת אתר" להוסיף</>}
      </div>
    );
  }

  return (
    <div>
      <div className={styles.sectionHeader}>אתרים פעילים</div>

      {sites.map((site) => (
        <div
          key={site.id}
          className={`${styles.card} ${site.id === activeSiteId ? styles.active : ""}`}
          onClick={() => setActiveSite(site.id)}
        >
          <span
            className={styles.freqBadge}
            style={{ background: FREQ_COLORS[site.frequency] ?? "var(--accent)" }}
          >
            {site.frequency} MHz
          </span>
          <div className={styles.name}>{site.name}</div>
          {site.params_source === "estimated" && (
            <span className={styles.estBadge} title={site.source_note ?? "נוצר מאתר סלולרי אזרחי עם פרמטרים משוערים"}>
              ⚠ משוער (אתר סלולרי)
            </span>
          )}
          <div className={styles.detail}>
            📍 {site.lat.toFixed(4)}, {site.lon.toFixed(4)}
          </div>
          <div className={styles.detail}>
            🏔 {site.elevation_m ?? 0}מ' ASL &nbsp;|&nbsp; 🏗 {site.ant_height}מ' AGL
            {site.ant_height_is_default && (
              <span
                title="גובה האנטנה הוא ערך ברירת מחדל ולא הוזן ידנית – לחץ 'ערוך' לעדכון"
                style={{ color: "var(--warn)", marginInlineStart: 4 }}
              >
                (ברירת מחדל)
              </span>
            )}
          </div>
          <div className={styles.detail}>
            ⚡ {site.tx_power} dBm &nbsp;|&nbsp; 📶 {site.rx_threshold} dBm
          </div>
          {site.notes && <div className={styles.notes}>{site.notes}</div>}
          {writable && <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
            <button
              className={styles.editBtn}
              onClick={(e) => { 
                e.stopPropagation(); 
                setEditingSite(site); 
                setActiveTab("new"); 
              }}
              style={{ flex: 1, padding: "6px", background: "#3b82f6", color: "white", border: "none", borderRadius: "4px", cursor: "pointer" }}
            >
              ✏️ ערוך
            </button>
            <button
              className={styles.deleteBtn}
              onClick={(e) => { e.stopPropagation(); deleteMut.mutate(site.id); }}
              style={{ flex: 1, padding: "6px", background: "#ef4444", color: "white", border: "none", borderRadius: "4px", cursor: "pointer" }}
            >
              ✕ מחק
            </button>
          </div>}
        </div>
      ))}

    </div>
  );
}
