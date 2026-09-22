"use client";

import { useState } from "react";
import Button from "@/components/ui/Button";
import { TextAreaField } from "@/components/ui/Field";
import { Toast } from "@/components/ui/Feedback";

export default function QuestionAnswerForm({ qid }: { qid: string }) {
  const [text, setText] = useState("");
  const [done, setDone] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

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
    setToast("回答を受け付けました。反映には少し時間がかかります。");
  }

  if (done) {
    return <Toast message={toast} onDone={() => setToast(null)} />;
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <TextAreaField
        label="回答"
        hideLabel
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        required
      />
      <Button type="submit" variant="primary" className="w-fit">
        回答する
      </Button>
    </form>
  );
}
