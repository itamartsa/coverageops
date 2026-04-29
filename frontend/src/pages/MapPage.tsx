import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { sitesApi } from "../services/api";
import { useStore } from "../store/useStore";
import TopBar from "../components/TopBar";
import Sidebar from "../components/Sidebar";
import MapView from "../components/MapView";
import Notification from "../components/Notification";
import styles from "./MapPage.module.css";

export default function MapPage() {
  const { setSites } = useStore();

  // Load sites from backend on mount
  const { data: sites } = useQuery({
    queryKey: ["sites"],
    queryFn: sitesApi.list,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (sites) setSites(sites);
  }, [sites, setSites]);

  return (
    <div className={styles.layout}>
      <TopBar />
      <div className={styles.body}>
        <Sidebar />
        <MapView />
      </div>
      <Notification />
    </div>
  );
}
