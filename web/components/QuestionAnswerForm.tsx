"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function QuestionAnswerForm({ qid }: { qid: string }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch(`/api/questions/${qid}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "回答の送信に失敗しました。");
        return;
      }
      setDone(true);
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return <p className="rounded bg-green-50 p-3 text-sm text-green-700">回答を送信しました。</p>;
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      <textarea
        className="rounded border px-3 py-2 text-sm"
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        required
      />
      <button
        type="submit"
        disabled={submitting}
        className="w-fit rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
      >
        回答する
      </button>
    </form>
  );
}
