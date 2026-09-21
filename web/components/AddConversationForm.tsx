"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Visibility } from "@/lib/worker-types";
import Button from "@/components/ui/Button";
import { SelectField, TextAreaField } from "@/components/ui/Field";
import { InlineError, InlineNotice } from "@/components/ui/Feedback";

export default function AddConversationForm({
  pid,
  onDone,
}: {
  pid: string;
  onDone?: (message: string) => void;
}) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("all");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [speakerWarning, setSpeakerWarning] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setSpeakerWarning(null);
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
      const done = `登録しました（${data.label_prefix}、区切り${data.segment_count}件、伏せ字${data.redaction_count}件）。`;
      setMessage(done);
      // AD-8：話者の目印（user・ai）がどちらも0件なら、出どころの印が付かないことを伝える。
      const counts = data.speaker_counts as
        { user: number; ai: number; unknown: number } | undefined;
      if (counts && counts.user === 0 && counts.ai === 0) {
        setSpeakerWarning(
          "話者の目印が見つからなかったため、出どころの印は付きません。",
        );
      }
      setText("");
      router.refresh();
      // 話者の警告があるときは、その場に残すため閉じない
      if (!(counts && counts.user === 0 && counts.ai === 0)) onDone?.(done);
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <InlineError>{error}</InlineError>
      {speakerWarning && (
        <InlineNotice>
          {message} {speakerWarning}
        </InlineNotice>
      )}
      <TextAreaField
        label="会話のログ"
        help="会話をそのまま貼ると、AIの提案かどうかの印がより正確になります。"
        rows={8}
        placeholder="会話のログを貼り付けてください"
        value={text}
        onChange={(e) => setText(e.target.value)}
        required
      />
      <SelectField
        label="公開範囲"
        value={visibility}
        onChange={(e) => setVisibility(e.target.value as Visibility)}
        className="max-w-xs"
      >
        <option value="all">全員</option>
        <option value="managers_only">マネージャーのみ</option>
      </SelectField>
      <div>
        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? "登録しています…" : "登録する"}
        </Button>
      </div>
    </form>
  );
}
