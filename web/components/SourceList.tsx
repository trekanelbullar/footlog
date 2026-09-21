"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { ExcludeReason, SourceSummary } from "@/lib/worker-types";

const TYPE_LABEL: Record<string, string> = {
  file: "ファイル",
  conversation: "会話",
  answer: "回答",
};
const REASON_OPTIONS: { value: ExcludeReason; label: string }[] = [
  { value: "private", label: "個人的な内容" },
  { value: "confidential", label: "機密情報" },
  { value: "irrelevant", label: "無関係な内容" },
  { value: "other", label: "その他" },
];

export default function SourceList({
  initialSources,
  myUserId,
  isManager,
}: {
  initialSources: SourceSummary[];
  myUserId: string;
  /** 権限表（§1.3）に合わせて、押しても 403 になる操作のボタンは出さない（判定の本体は worker）。 */
  isManager: boolean;
}) {
  const router = useRouter();
  const [reasons, setReasons] = useState<Record<string, ExcludeReason>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reasonFor = (sid: string) => reasons[sid] ?? "other";

  async function toggleVisibility(s: SourceSummary) {
    setError(null);
    setBusy(s.source_id);
    try {
      const nextVisibility = s.visibility === "all" ? "managers_only" : "all";
      const res = await fetch(`/api/sources/${s.source_id}/visibility`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          visibility: nextVisibility,
          reason: reasonFor(s.source_id),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "変更に失敗しました。");
        return;
      }
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setBusy(null);
    }
  }

  async function toggleExclusion(s: SourceSummary) {
    setError(null);
    setBusy(s.source_id);
    try {
      const res = await fetch(`/api/sources/${s.source_id}/exclusion`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          is_excluded: !s.is_excluded,
          reason: reasonFor(s.source_id),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "変更に失敗しました。");
        return;
      }
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setBusy(null);
    }
  }

  async function openDownload(s: SourceSummary) {
    setError(null);
    setBusy(s.source_id);
    try {
      const res = await fetch(`/api/sources/${s.source_id}/download-url`);
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "取得に失敗しました。");
        return;
      }
      window.open(data.url, "_blank", "noopener,noreferrer");
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setBusy(null);
    }
  }

  if (initialSources.length === 0) {
    return <p className="text-sm text-gray-500">まだソースがありません。</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="text-gray-500">
            <tr>
              <th className="py-1 pr-2">番号</th>
              <th className="py-1 pr-2">種類</th>
              <th className="py-1 pr-2">名前</th>
              <th className="py-1 pr-2">登録者</th>
              <th className="py-1 pr-2">公開範囲</th>
              <th className="py-1 pr-2">除外</th>
              <th className="py-1 pr-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {initialSources.map((s) => (
              <tr key={s.source_id} className="border-t">
                <td className="py-2 pr-2">S{s.source_no}</td>
                <td className="py-2 pr-2">{TYPE_LABEL[s.type] ?? s.type}</td>
                <td className="py-2 pr-2">{s.filename ?? "（会話）"}</td>
                <td className="py-2 pr-2">
                  {s.uploaded_by === myUserId ? "自分" : "他のメンバー"}
                </td>
                <td className="py-2 pr-2">
                  {s.visibility === "all" ? "全員" : "マネージャーのみ"}
                </td>
                <td className="py-2 pr-2">
                  {s.is_excluded
                    ? `除外中（${s.exclude_reason ?? "-"}）`
                    : "なし"}
                </td>
                <td className="py-2 pr-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {(isManager || s.uploaded_by === myUserId) && (
                      <select
                        className="rounded border px-1 py-0.5 text-xs"
                        value={reasonFor(s.source_id)}
                        onChange={(e) =>
                          setReasons((r) => ({
                            ...r,
                            [s.source_id]: e.target.value as ExcludeReason,
                          }))
                        }
                      >
                        {REASON_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    )}
                    {(isManager ||
                      (s.uploaded_by === myUserId &&
                        s.visibility === "all")) && (
                      <button
                        type="button"
                        disabled={busy === s.source_id}
                        onClick={() => toggleVisibility(s)}
                        className="rounded border px-2 py-1 text-xs hover:bg-gray-50 disabled:opacity-50"
                      >
                        {s.visibility === "all"
                          ? "マネージャー限定に"
                          : "全員公開に"}
                      </button>
                    )}
                    {(isManager || s.uploaded_by === myUserId) && (
                      <button
                        type="button"
                        disabled={busy === s.source_id}
                        onClick={() => toggleExclusion(s)}
                        className="rounded border px-2 py-1 text-xs hover:bg-gray-50 disabled:opacity-50"
                      >
                        {s.is_excluded ? "除外を戻す" : "除外する"}
                      </button>
                    )}
                    {s.type === "file" && (
                      <button
                        type="button"
                        disabled={busy === s.source_id}
                        onClick={() => openDownload(s)}
                        className="rounded border px-2 py-1 text-xs hover:bg-gray-50 disabled:opacity-50"
                      >
                        ダウンロード
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
