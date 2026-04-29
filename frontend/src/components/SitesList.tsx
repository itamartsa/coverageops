import { useMutation, useQueryClient } from "@tanstack/react-query";
import { sitesApi } from "../services/api";
import { useStore } from "../store/useStore";
import { notify } from "./Notification";
import styles from "./SitesList.module.css";

const FREQ_COLORS: Record<number, string> = {
  700: "#ff6b35", 850: "#ffb347", 900:  "#00ff88",
  1800: "#00d4ff", 2100: "#a78bfa", 2600: "#f472b6",
  3500: "#f59e0b",
};

export default function SitesList() {
  const { sites, activeSiteId, setActiveSite, removeSite, setEditingSite,
          setDrawMode, setActiveTab, setBbox, setCoverageResult,
          setSelectedSiteIds } = useStore();
  const qc = useQueryClient();

  const deleteMut = useMutation({
    mutationFn: (id: number) => sitesApi.delete(id),
    onSuccess: (_, id) => {
      removeSite(id);
      setCoverageResult(null);
      setSelectedSiteIds(useStore.getState().selectedSiteIds.filter((x) => x !== id));
      qc.invalidateQueries({ queryKey: ["sites"] });
      notify("אתר נמחק");
    },
  });

  if (!sites.length) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyIcon}>📡</div>
        אין אתרי קשר מוגדרים<br />עבור ל"הקמת אתר" להוסיף
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
          <div className={styles.detail}>
            📍 {site.lat.toFixed(4)}, {site.lon.toFixed(4)}
          </div>
          <div className={styles.detail}>
            🏔 {site.elevation_m ?? 0}מ' ASL &nbsp;|&nbsp; 🏗 {site.ant_height}מ' AGL
          </div>
          <div className={styles.detail}>
            ⚡ {site.tx_power} dBm &nbsp;|&nbsp; 📶 {site.rx_threshold} dBm
          </div>
          {site.notes && <div className={styles.notes}>{site.notes}</div>}
          <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
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
          </div>
        </div>
      ))}

    </div>
  );
}
