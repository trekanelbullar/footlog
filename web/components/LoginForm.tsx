"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "ログインに失敗しました。");
        return;
      }
      router.push("/projects");
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      <label className="flex flex-col gap-1 text-sm">
        メールアドレス
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          className="rounded border px-3 py-2"
          placeholder="manager@example.com"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        パスワード
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          className="rounded border px-3 py-2"
        />
      </label>
      <button
        type="submit"
        disabled={submitting}
        className="mt-2 rounded bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
      >
        ログイン
      </button>
    </form>
  );
}
