"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Button from "@/components/ui/Button";
import { TextField, TextAreaField } from "@/components/ui/Field";
import { InlineError } from "@/components/ui/Feedback";

export default function CreateProjectForm({
  onDone,
}: {
  onDone?: (message: string) => void;
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [thresholdHours, setThresholdHours] = useState("24");
  const [excludeWeekends, setExcludeWeekends] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          goal_description: goal,
          no_progress_threshold_hours: Number(thresholdHours) || 24,
          exclude_weekends: excludeWeekends,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "作成に失敗しました。");
        return;
      }
      onDone?.(`${name} を作成しました。`);
      router.push(`/projects/${data.project_id}`);
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex max-w-lg flex-col gap-3 rounded border bg-white p-4"
    >
      <InlineError>{error}</InlineError>
      <TextField
        label="プロジェクト名"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
      />
      <TextAreaField
        label="目的の説明"
        rows={3}
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
        required
      />
      <TextField
        label="無進捗としきい値（時間）"
        type="number"
        min={1}
        value={thresholdHours}
        onChange={(e) => setThresholdHours(e.target.value)}
      />
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={excludeWeekends}
          onChange={(e) => setExcludeWeekends(e.target.checked)}
        />
        無進捗の判定で土日を除く
      </label>
      <Button
        type="submit"
        variant="primary"
        disabled={submitting}
        className="mt-2 w-fit"
      >
        作成する
      </Button>
    </form>
  );
}
