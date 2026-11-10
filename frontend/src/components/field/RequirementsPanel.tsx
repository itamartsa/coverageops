/**
 * "דרישות מערכת ליחידות הקצה" (product-owner request) — rendered from
 * GET /api/field/meta/device-requirements (derived from ANDROID_APP_SPEC.md). Items the Android
 * spec marks [לאימות] are visibly flagged as not yet verified.
 */
import { useQuery } from "@tanstack/react-query";
import { fieldApi } from "../../services/api";
import { Dialog, ErrorState, Loading } from "./primitives";
import s from "./field.module.css";
import r from "./RequirementsPanel.module.css";

const TO_VERIFY = "לאימות";

export default function RequirementsPanel({ onClose }: { onClose: () => void }) {
  const q = useQuery({ queryKey: ["field", "device-requirements"], queryFn: fieldApi.deviceRequirements, staleTime: 3600_000 });
  const d = q.data;
  const nVerify = d?.sections.reduce((a, sec) => a + sec.items.filter((i) => i.status === TO_VERIFY).length, 0) ?? 0;
  return (
    <Dialog title="דרישות מערכת ליחידות הקצה" onClose={onClose} wide labelledBy="req-title">
      <div data-testid="requirements-panel">
        {q.isLoading ? <Loading lines={5} /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : d && (
          <>
            <div className={r.headline}>
              <span className={r.os} aria-hidden="true">🤖</span>
              <div>
                <div className={r.hTitle}>האפליקציה דורשת <bdi dir="ltr">Android 11 (API 30)</bdi> ומעלה</div>
                <div className={s.muted}>{d.title_he}</div>
              </div>
            </div>
            <ul className={r.key} aria-label="מקרא סטטוס">
              {Object.entries(d.status_legend_he).map(([k, v]) => (
                <li key={k}><StatusTag status={k} /> {v}</li>
              ))}
              {nVerify > 0 && <li className={s.muted}>{nVerify} פריטים מסומנים "לאימות".</li>}
            </ul>
            {d.sections.map((sec) => (
              <section key={sec.key} className={r.sec} aria-labelledby={`req-${sec.key}`}>
                <h3 id={`req-${sec.key}`}>{sec.title_he}</h3>
                <ul>
                  {sec.items.map((it, i) => (
                    <li key={i} className={it.status === TO_VERIFY ? r.toVerify : undefined} data-req-status={it.status}>
                      <StatusTag status={it.status} />
                      <div>
                        <div>{it.text_he}</div>
                        {it.note_he && <div className={s.muted} style={{ fontSize: 11 }}>{it.note_he}</div>}
                        <div className={r.src}>מקור: אפיון האנדרואיד §{it.source}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            <p className={s.note}>המקור: <code>{d.spec_document}</code>. פריטי "לאימות" טרם נבדקו במכשיר או בתיעוד ואינם דרישה מאושרת.</p>
          </>
        )}
      </div>
    </Dialog>
  );
}

function StatusTag({ status }: { status: string }) {
  const verify = status === TO_VERIFY;
  return (
    <span className={`${r.tag} ${verify ? r.tagVerify : r.tagOk}`}>
      <span aria-hidden="true">{verify ? "?" : "✓"}</span> {verify ? "לאימות" : "מאומת באפיון"}
    </span>
  );
}
