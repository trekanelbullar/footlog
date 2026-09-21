"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import type { NotificationItem } from "@/lib/worker-types";
import { formatJst } from "@/lib/format";
import Button from "@/components/ui/Button";
import { InlineError } from "@/components/ui/Feedback";

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
      const res = await fetch(`/api/me/notifications/${n.id}/read`, {
        method: "POST",
      });
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
    if (n.kind === "question" && n.question_id)
      return `/questions/${n.question_id}`;
    return `/projects/${n.project_id}`;
  }

  if (initialNotifications.length === 0) {
    return <p className="text-sm text-gray-500">通知はありません。</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      <InlineError>{error}</InlineError>
      <ul className="flex flex-col gap-2">
        {initialNotifications.map((n) => (
          <li
            key={n.id}
            className={`flex items-center justify-between gap-3 rounded border p-3 ${n.read_at ? "bg-white" : "bg-blue-50"}`}
          >
            <Link href={href(n)} className="min-w-0 flex-1">
              <span className="mr-2 text-xs text-gray-500">
                [{KIND_LABEL[n.kind] ?? n.kind}]
              </span>
              {n.title}
            </Link>
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-xs text-gray-400">
                {formatJst(n.created_at)}
              </span>
              {!n.read_at && (
                <Button variant="ghost" onClick={() => markRead(n)}>
                  既読にする
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
