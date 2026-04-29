import { useState, useRef } from "react";
import styles from "./InfoTooltip.module.css";

interface Props {
  text: string;
}

export default function InfoTooltip({ text }: Props) {
  const [visible, setVisible] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function show() {
    if (timerRef.current) clearTimeout(timerRef.current);
    setVisible(true);
  }
  function hide() {
    timerRef.current = setTimeout(() => setVisible(false), 120);
  }

  return (
    <span className={styles.wrap} onMouseEnter={show} onMouseLeave={hide}>
      <span className={styles.icon}>ⓘ</span>
      {visible && (
        <span className={styles.tooltip} onMouseEnter={show} onMouseLeave={hide}>
          {text}
        </span>
      )}
    </span>
  );
}
