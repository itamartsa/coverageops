import { useState, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { sitesApi, type SiteCreate } from "../services/api";
import { useStore } from "../store/useStore";
import { notify } from "./Notification";
import InfoTooltip from "./InfoTooltip";
import styles from "./SiteForm.module.css";

const FREQUENCIES = [700, 850, 900, 1800, 2100, 2600, 3500];
const FREQ_LABELS: Record<number, string> = {
  700:  "700 MHz – LTE Band 28",
  850:  "850 MHz – UMTS/LTE",
  900:  "900 MHz – GSM/LTE",
  1800: "1800 MHz – LTE Band 3",
  2100: "2100 MHz – UMTS/LTE",
  2600: "2600 MHz – LTE Band 7",
  3500: "3500 MHz – 5G NR Band n78",
};

const TIPS = {
  ant_height:   "גובה האנטנה מעל הקרקע (מטרים).\nכל הכפלת גובה מוסיפה ~6 dB = מגדילה טווח בכ-40%.\nמשפיע ישירות על שיפור הכיסוי בשטח מחופה.",
  elevation_m:  "גובה האתר מעל פני הים (מטרים, ASL).\nמשפיע על ניתוח פרופיל הטרן בין האתר לנקודת הקצה.\nאתר בפסגה גבוהה = כיסוי טוב יותר לשטחים נמוכים.",
  frequency:    "תדר השידור של האנטנה.\n↑ תדר → ↓ טווח, ↑ קיבולת.\n700 MHz = טווח ארוך (כפרי). 3500 MHz = מהיר אך קצר (עירוני).",
  tx_power:     "הספק השידור (dBm = דציבל-מיליוואט).\n↑ הספק → ↑ טווח, אך ↑ הפרעות לאחרים.\nמוגבל ע\"י רגולציה. כל +3 dBm = פי 2 בהספק.",
  rx_threshold: "עוצמת האות המינימלית לקליטה (dBm).\n↓ ערך (שלילי יותר) = רגישות גבוהה יותר = קליטה מרחוק גדול יותר.\nדוגמה: -90 dBm נפוץ ב-4G, -110 dBm ב-LTE Advanced.",
  max_radius:   "הרדיוס המקסימלי לסריקה בניתוח הכיסוי (ק\"מ).\nאינו מייצג כיסוי בפועל – רק גבולות החישוב.\nמגדיל זמן חישוב.",
};

const defaults = {
  name: "", lat: "", lon: "",
  ant_height: 6, frequency: 900,
  tx_power: 43, rx_threshold: -90,
  max_radius: 350, notes: "",
};

export default function SiteForm() {
  const [form, setForm] = useState(defaults);
  const { addSite, setActiveSite, setActiveTab, setDrawMode, pickedLocation, setPickedLocation, editingSite, setEditingSite } = useStore();
  const qc = useQueryClient();

  useEffect(() => {
    if (pickedLocation) {
      setForm((f) => ({ ...f, lat: pickedLocation.lat.toFixed(5), lon: pickedLocation.lon.toFixed(5) }));
      setPickedLocation(null);
    }
  }, [pickedLocation, setPickedLocation]);

  useEffect(() => {
    if (editingSite) {
      setForm({
        name: editingSite.name,
        lat: editingSite.lat.toString(),
        lon: editingSite.lon.toString(),
        ant_height: editingSite.ant_height,
        frequency: editingSite.frequency,
        tx_power: editingSite.tx_power,
        rx_threshold: editingSite.rx_threshold,
        max_radius: editingSite.max_radius,
        notes: editingSite.notes || "",
      });
    } else {
      setForm(defaults);
    }
  }, [editingSite]);

  const mut = useMutation({
    mutationFn: (s: SiteCreate) => {
      if (editingSite) return sitesApi.update(editingSite.id, s);
      return sitesApi.create(s);
    },
    onSuccess: (site) => {
      if (!editingSite) { addSite(site); setActiveSite(site.id); }
      qc.invalidateQueries({ queryKey: ["sites"] });
      setActiveTab("sites");
      setEditingSite(null);
      setForm(defaults);
      notify(`✅ אתר "${site.name}" ${editingSite ? "עודכן" : "נוסף"}`);
    },
    onError: (err: any) => {
      notify(`⚠ ${err.response?.data?.detail ?? "שגיאה בשמירת האתר"}`);
    },
  });

  function set(field: string, value: any) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) { notify("⚠ נא להזין שם אתר"); return; }
    const lat = parseFloat(form.lat as string);
    const lon = parseFloat(form.lon as string);
    if (isNaN(lat) || isNaN(lon)) { notify("⚠ נא להזין קואורדינטות תקינות"); return; }

    mut.mutate({
      name: form.name, lat, lon,
      ant_height: form.ant_height,
      frequency: form.frequency,
      tx_power: form.tx_power,
      rx_threshold: form.rx_threshold,
      max_radius: form.max_radius,
      notes: form.notes || undefined,
    });
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className={styles.info}>
        💡 לחץ "בחר מהמפה" לקליטת קואורדינטות בלחיצה על המפה
      </div>

      {/* Name */}
      <div className={styles.group}>
        <label className={styles.label}>שם האתר</label>
        <input className={styles.input} value={form.name}
          onChange={e => set("name", e.target.value)}
          placeholder="לדוגמה: אתר גבעה 27" />
      </div>

      {/* Coords */}
      <div className={styles.row}>
        <div className={styles.group}>
          <label className={styles.label}>קו רוחב (Lat)</label>
          <input className={styles.input} type="number" step="0.0001"
            value={form.lat} onChange={e => set("lat", e.target.value)}
            placeholder="31.7683" />
        </div>
        <div className={styles.group}>
          <label className={styles.label}>קו אורך (Lon)</label>
          <input className={styles.input} type="number" step="0.0001"
            value={form.lon} onChange={e => set("lon", e.target.value)}
            placeholder="35.2137" />
        </div>
      </div>

      <button type="button" className={styles.mapPickBtn}
        onClick={() => { setDrawMode("point"); notify("לחץ על המפה לבחירת מיקום"); }}>
        📍 בחר מיקום מהמפה
      </button>

      <div className={styles.divider}>מאפייני אנטנה</div>

      {/* Antenna height */}
      <div className={styles.group}>
        <label className={styles.label}>
          גובה אנטנה מעל קרקע (מ')
          <InfoTooltip text={TIPS.ant_height} />
        </label>
        <input className={styles.input} type="number" min="1" max="200"
          value={form.ant_height} onChange={e => set("ant_height", +e.target.value)} />
      </div>

      {/* Frequency */}
      <div className={styles.group}>
        <label className={styles.label}>
          תדר סלולר
          <InfoTooltip text={TIPS.frequency} />
        </label>
        <select className={styles.select} value={form.frequency}
          onChange={e => set("frequency", +e.target.value)}>
          {FREQUENCIES.map(f => (
            <option key={f} value={f}>{FREQ_LABELS[f]}</option>
          ))}
        </select>
      </div>

      {/* Power / threshold */}
      <div className={styles.row}>
        <div className={styles.group}>
          <label className={styles.label}>
            הספק שידור (dBm)
            <InfoTooltip text={TIPS.tx_power} />
          </label>
          <input className={styles.input} type="number" min="0" max="70"
            value={form.tx_power} onChange={e => set("tx_power", +e.target.value)} />
        </div>
        <div className={styles.group}>
          <label className={styles.label}>
            סף קליטה (dBm)
            <InfoTooltip text={TIPS.rx_threshold} />
          </label>
          <input className={styles.input} type="number" min="-130" max="-40"
            value={form.rx_threshold} onChange={e => set("rx_threshold", +e.target.value)} />
        </div>
      </div>

      {/* Radius */}
      <div className={styles.group}>
        <label className={styles.label}>
          רדיוס ניתוח מקסימלי (ק"מ)
          <InfoTooltip text={TIPS.max_radius} />
        </label>
        <input className={styles.input} type="number" min="1" max="350"
          value={form.max_radius} onChange={e => set("max_radius", +e.target.value)} />
      </div>

      {/* Notes */}
      <div className={styles.group}>
        <label className={styles.label}>הערות</label>
        <input className={styles.input} value={form.notes}
          onChange={e => set("notes", e.target.value)}
          placeholder="הערה חופשית" />
      </div>

      <div style={{ display: "flex", gap: "10px" }}>
        <button className={styles.submitBtn} type="submit" disabled={mut.isPending} style={{ flex: 1 }}>
          {mut.isPending ? "שומר..." : (editingSite ? "💾 שמור שינויים" : "+ הוסף אתר קשר")}
        </button>
        {editingSite && (
          <button
            type="button"
            onClick={() => { setEditingSite(null); setForm(defaults); setActiveTab("sites"); }}
            style={{ flex: 1, background: "#ef4444", color: "white", padding: "12px", border: "none", borderRadius: "8px", fontWeight: "bold", cursor: "pointer" }}
          >
            ❌ ביטול עריכה
          </button>
        )}
      </div>
    </form>
  );
}
