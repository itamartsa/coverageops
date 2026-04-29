import { useEffect, useState } from "react";
import styles from "./Notification.module.css";

// Singleton event bus
const listeners: Set<(msg: string) => void> = new Set();

export function notify(msg: string) {
  listeners.forEach((fn) => fn(msg));
}

export default function Notification() {
  const [messages, setMessages] = useState<{ id: number; text: string }[]>([]);

  useEffect(() => {
    let counter = 0;
    function handler(msg: string) {
      const id = counter++;
      setMessages((prev) => [...prev, { id, text: msg }]);
      setTimeout(() => {
        setMessages((prev) => prev.filter((m) => m.id !== id));
      }, 3200);
    }
    listeners.add(handler);
    return () => { listeners.delete(handler); };
  }, []);

  return (
    <div className={styles.container}>
      {messages.map((m) => (
        <div key={m.id} className={styles.toast}>{m.text}</div>
      ))}
    </div>
  );
}
