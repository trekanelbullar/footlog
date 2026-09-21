"use client";

import { useState } from "react";

export default function QuestionAnswerForm({ qid }: { qid: string }) {
  const [text, setText] = useState("");
  const [done, setDone] = useState(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // design.md §5.4・§5.1 と同じ扱い：worker（W23）はこのリクエストの中で
    // 取り込みと実行を最後まで走らせるため、応答に数分かかることがある。
    // 画面は応答を待たずに「受け付けました」を出す（W15 の実行と同じ考え方）。
    fetch(`/api/questions/${qid}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).catch(() => {});
    setDone(true);
  }

  if (done) {
    return (
      <p className="rounded bg-green-50 p-3 text-sm text-green-700">
        回答を受け付けました。反映には少し時間がかかります。
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <textarea
        className="rounded border px-3 py-2 text-sm"
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        required
      />
      <button
        type="submit"
        className="w-fit rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
      >
        回答する
      </button>
    </form>
  );
}
