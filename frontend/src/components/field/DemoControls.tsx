/**
 * Demo fleet controls (ADMIN). Loads / removes the labelled demo group ("דמו") through the API,
 * so it lands in whichever database the running server uses (Docker/PostgreSQL or local SQLite).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiErrorText, fieldApi } from "../../services/api";
import s from "./field.module.css";

export default function DemoControls({ compact = false }: { compact?: boolean }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const statusQ = useQuery({ queryKey: ["field", "demo"], queryFn: fieldApi.demoStatus, retry: false });
  const done = () => { setErr(null); qc.invalidateQueries({ queryKey: ["field"] }); };
  const seed = useMutation({ mutationFn: fieldApi.demoSeed, onSuccess: done, onError: (e) => setErr(apiErrorText(e, "טעינת נתוני הדמו נכשלה")) });
  const remove = useMutation({ mutationFn: fieldApi.demoRemove, onSuccess: done, onError: (e) => setErr(apiErrorText(e, "הסרת נתוני הדמו נכשלה")) });

  const st = statusQ.data;
  if (!st || !st.enabled) return null;
  const busy = seed.isPending || remove.isPending;
  const has = st.devices > 0;
  return (
    <div data-testid="demo-controls" style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
      {!compact && (
        <span className={s.muted}>
          {has ? <>נטען צי דמו: <bdi dir="ltr">{st.devices}</bdi> מכשירים בקבוצה "{st.group}"</> : "אין נתוני דמו"}
        </span>
      )}
      <button className={has ? s.btn : s.btnPrimary} disabled={busy} data-testid="demo-seed"
        onClick={() => { setErr(null); seed.mutate(); }}>
        {seed.isPending ? "טוען נתוני דמו… (עד דקה)" : has ? "רענן נתוני דמו" : "טען נתוני דמו"}
      </button>
      {has && (
        <button className={s.btnGhost} disabled={busy} data-testid="demo-remove"
          onClick={() => { setErr(null); remove.mutate(); }}>
          {remove.isPending ? "מסיר…" : "הסר נתוני דמו"}
        </button>
      )}
      {err && <span className={s.errbox} role="alert">{err}</span>}
    </div>
  );
}
