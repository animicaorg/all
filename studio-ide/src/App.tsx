import { useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { useAuthStore } from "@/state/auth";
import { IdeShell } from "@/shell/IdeShell";
import { Welcome } from "@/shell/Welcome";

function DesktopRedirect() {
  useEffect(() => {
    window.location.href = "/go";
  }, []);
  return <div className="grid h-full place-items-center text-muted">Opening the desktop Studio…</div>;
}

export default function App() {
  const { me, loading, fetchMe } = useAuthStore();

  useEffect(() => {
    fetchMe();
  }, [fetchMe]);

  if (loading && !me) {
    return (
      <div className="grid h-full place-items-center text-muted">
        <div className="flex flex-col items-center gap-3">
          <div className="h-7 w-7 animate-spin rounded-full border-2 border-border border-t-accent" />
          <span className="text-sm">Loading Studio…</span>
        </div>
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/desktop" element={<DesktopRedirect />} />
      <Route
        path="/*"
        element={me?.authed ? <IdeShell /> : <Welcome />}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
