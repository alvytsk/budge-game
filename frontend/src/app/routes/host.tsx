import { createFileRoute, Outlet } from "@tanstack/react-router";
import { useAuthGate, useLogout } from "@/features/session";
import { LoginPage } from "@/pages/host-login";

export const Route = createFileRoute("/host")({ component: HostLayout });

function HostLayout() {
  const { state } = useAuthGate();
  const logOut = useLogout();

  if (state === "checking") {
    return <main className="flex h-full items-center justify-center text-stage-muted">…</main>;
  }
  if (state === "out") return <LoginPage />;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-white/10 border-b px-8 py-4">
        <span className="font-display text-2xl uppercase">budge</span>
        <button
          type="button"
          onClick={() => void logOut()}
          className="text-sm text-stage-muted underline"
        >
          Выйти
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        <Outlet />
      </div>
    </div>
  );
}
