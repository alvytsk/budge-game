import { type FormEvent, useState } from "react";
import { useLogin } from "@/features/session";

export function LoginPage() {
  const { logIn, pending, failed } = useLogin();
  const [password, setPassword] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    await logIn(password);
  }

  return (
    <main className="flex h-full items-center justify-center">
      <form onSubmit={submit} className="flex w-80 flex-col gap-4">
        <h1 className="font-display text-4xl uppercase">budge</h1>
        <label className="flex flex-col gap-2 text-sm text-stage-muted">
          Пароль
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded-lg bg-white/10 px-4 py-3 text-lg text-stage-ink outline-none focus:bg-white/15"
          />
        </label>
        {failed && <p className="text-red-400">Неверный пароль</p>}
        <button
          type="submit"
          disabled={pending}
          className="rounded-lg bg-white/15 px-4 py-3 font-display text-xl uppercase disabled:opacity-50"
        >
          Войти
        </button>
      </form>
    </main>
  );
}
