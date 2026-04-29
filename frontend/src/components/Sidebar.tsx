import { useStore } from "../store/useStore";
import SitesList from "./SitesList";
import SiteForm from "./SiteForm";
import AnalysisPanel from "./AnalysisPanel";
import HistoryPanel from "./HistoryPanel";
import CrossSectionPanel from "./CrossSectionPanel";
import LayersPanel from "./LayersPanel";
import styles from "./Sidebar.module.css";

const TABS = [
  { key: "sites",        label: "אתרי קשר" },
  { key: "new",          label: "הקמת אתר" },
  { key: "analysis",     label: "ניתוח"    },
  { key: "crosssection", label: "חתך רדיו" },
  { key: "layers",       label: "🗂 שכבות"  },
  { key: "history",      label: "היסטוריה" },
] as const;

export default function Sidebar() {
  const { activeTab, setActiveTab, editingSite } = useStore();

  return (
    <aside className={styles.sidebar}>
      <nav className={styles.tabs}>
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`${styles.tab} ${activeTab === t.key ? styles.active : ""}`}
            onClick={() => setActiveTab(t.key)}
          >
            {t.key === "new" && editingSite ? "עריכת אתר" : t.label}
          </button>
        ))}
      </nav>

      <div className={styles.content}>
        {activeTab === "sites"        && <SitesList />}
        {activeTab === "new"          && <SiteForm />}
        {activeTab === "analysis"     && <AnalysisPanel />}
        {activeTab === "crosssection" && <CrossSectionPanel />}
        {activeTab === "layers"       && <LayersPanel />}
        {activeTab === "history"      && <HistoryPanel />}
      </div>
    </aside>
  );
}
