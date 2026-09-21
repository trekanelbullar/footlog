"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Visibility } from "@/lib/worker-types";

export default function AddConversationForm({ pid }: { pid: string }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("all");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setSubmitting(true);
    try {
      const res = await fetch(`/api/projects/${pid}/sources/conversation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, visibility }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "登録に失敗しました。");
        return;
      }
      setMessage(
        `登録しました（${data.label_prefix}、区切り${data.segment_count}件、伏せ字${data.redaction_count}件）。`
      );
      setText("");
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
      {message && <p className="rounded bg-green-50 p-2 text-sm text-green-700">{message}</p>}
      <textarea
        className="rounded border px-3 py-2 text-sm"
        rows={6}
        placeholder="会話のログを貼り付けてください"
        value={text}
        onChange={(e) => setText(e.target.value)}
        required
      />
      <label className="flex items-center gap-2 text-sm">
        公開範囲
        <select
          className="rounded border px-2 py-1"
          value={visibility}
          onChange={(e) => setVisibility(e.target.value as Visibility)}
        >
          <option value="all">全員</option>
          <option value="managers_only">マネージャーのみ</option>
        </select>
      </label>
      <button
        type="submit"
        disabled={submitting}
        className="w-fit rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
      >
        登録する
      </button>
    </form>
  );
}
