"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { NotificationItem } from "@/lib/worker-types";

const KIND_LABEL: Record<string, string> = {
  question: "質問",
  report: "レポート",
  no_progress: "無進捗",
  cost_limited: "費用上限",
};

export default function NotificationList({
  initialNotifications,
}: {
  initialNotifications: NotificationItem[];
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  async function markRead(n: NotificationItem) {
    if (n.read_at) return;
    setError(null);
    try {
      const res = await fetch(`/api/me/notifications/${n.id}/read`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json();
        setError(data.message ?? "既読にできませんでした。");
        return;
      }
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    }
  }

  function href(n: NotificationItem): string {
    if (n.kind === "question" && n.question_id) return `/questions/${n.question_id}`;
    return `/projects/${n.project_id}`;
  }

  if (initialNotifications.length === 0) {
    return <p className="text-sm text-gray-500">通知はありません。</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      <ul className="flex flex-col gap-2">
        {initialNotifications.map((n) => (
          <li
            key={n.id}
            className={`rounded border p-3 ${n.read_at ? "bg-white" : "bg-blue-50"}`}
          >
            <a href={href(n)} onClick={() => markRead(n)} className="flex items-center justify-between">
              <span>
                <span className="mr-2 text-xs text-gray-500">[{KIND_LABEL[n.kind] ?? n.kind}]</span>
                {n.title}
              </span>
              <span className="text-xs text-gray-400">{n.created_at}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
