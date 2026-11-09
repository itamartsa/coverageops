import { useStore } from "../store/useStore";
import SitesList from "./SitesList";
import SiteForm from "./SiteForm";
import AnalysisPanel from "./AnalysisPanel";
import HistoryPanel from "./HistoryPanel";
import CrossSectionPanel from "./CrossSectionPanel";
import LayersPanel from "./LayersPanel";
import LinkPanel from "./LinkPanel";
import { canWrite } from "../lib/roles";
import styles from "./Sidebar.module.css";

const TABS = [
  { key: "sites",        label: "אתרי קשר" },
  { key: "new",          label: "הקמת אתר" },
  { key: "analysis",     label: "ניתוח"    },
  { key: "crosssection", label: "חתך רדיו" },
  { key: "link",         label: "🎯 קישור נקודתי" },
  { key: "layers",       label: "🗂 שכבות"  },
  { key: "history",      label: "היסטוריה" },
] as const;

export default function Sidebar() {
  const { activeTab, setActiveTab, editingSite, user } = useStore();
  const writable = canWrite(user?.role);
  // VIEWER is read-only: no "new site" tab (and never render the form, e.g. from a persisted tab)
  const tabs = TABS.filter((t) => writable || t.key !== "new");

  return (
    <aside className={styles.sidebar}>
      <nav className={styles.tabs}>
        {tabs.map((t) => (
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
        {activeTab === "new" && writable && <SiteForm />}
        {activeTab === "analysis"     && <AnalysisPanel />}
        {activeTab === "crosssection" && <CrossSectionPanel />}
        {activeTab === "link"         && <LinkPanel />}
        {activeTab === "layers"       && <LayersPanel />}
        {activeTab === "history"      && <HistoryPanel />}
      </div>
    </aside>
  );
}
