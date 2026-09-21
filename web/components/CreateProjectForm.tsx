"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function CreateProjectForm() {
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
      router.push(`/projects/${data.project_id}`);
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex max-w-lg flex-col gap-3 rounded border bg-white p-4">
      {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      <label className="flex flex-col gap-1 text-sm">
        プロジェクト名
        <input
          className="rounded border px-3 py-2"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        目的の説明
        <textarea
          className="rounded border px-3 py-2"
          rows={3}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          required
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        無進捗としきい値（時間）
        <input
          type="number"
          min={1}
          className="rounded border px-3 py-2"
          value={thresholdHours}
          onChange={(e) => setThresholdHours(e.target.value)}
        />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={excludeWeekends}
          onChange={(e) => setExcludeWeekends(e.target.checked)}
        />
        無進捗の判定で土日を除く
      </label>
      <button
        type="submit"
        disabled={submitting}
        className="mt-2 w-fit rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
      >
        作成する
      </button>
    </form>
  );
}
