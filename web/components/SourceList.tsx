"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { ExcludeReason, SourceSummary } from "@/lib/worker-types";
import Button from "@/components/ui/Button";
import { SelectField } from "@/components/ui/Field";
import { InlineError } from "@/components/ui/Feedback";

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
    return <p className="text-sm text-gray-500">まだ資料がありません。</p>;
  }

  const reasonLabel = (r: string | null) =>
    REASON_OPTIONS.find((o) => o.value === r)?.label ?? "その他";

  return (
    <div className="flex flex-col gap-3">
      <InlineError>{error}</InlineError>
      <ul className="divide-y divide-gray-200 rounded-md border border-gray-200">
        {initialSources.map((s) => {
          const canExclude = isManager || s.uploaded_by === myUserId;
          const canVisibility =
            isManager || (s.uploaded_by === myUserId && s.visibility === "all");
          return (
            <li
              key={s.source_id}
              className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-gray-900">
                  S{s.source_no}　{s.filename ?? "会話"}
                </p>
                <p className="text-xs text-gray-500">
                  {TYPE_LABEL[s.type] ?? s.type} ・{" "}
                  {s.uploaded_by === myUserId ? "自分" : "他のメンバー"}が登録
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-1 sm:justify-end">
                {canExclude && !s.is_excluded && (
                  <SelectField
                    label={`S${s.source_no} を除外する理由`}
                    hideLabel
                    className="w-40"
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
                  </SelectField>
                )}
                {canExclude && (
                  <Button
                    variant="ghost"
                    disabled={busy === s.source_id}
                    onClick={() => toggleExclusion(s)}
                  >
                    {s.is_excluded ? "除外を戻す" : "除外する"}
                  </Button>
                )}
                {canVisibility && (
                  <Button
                    variant="ghost"
                    disabled={busy === s.source_id}
                    onClick={() => toggleVisibility(s)}
                  >
                    {s.visibility === "all"
                      ? "マネージャー限定に"
                      : "全員公開に"}
                  </Button>
                )}
                {s.type === "file" && (
                  <Button
                    variant="ghost"
                    disabled={busy === s.source_id}
                    onClick={() => openDownload(s)}
                  >
                    ダウンロード
                  </Button>
                )}
                <span
                  className={`ml-1 shrink-0 rounded-full border px-2 py-0.5 text-xs ${
                    s.is_excluded
                      ? "border-gray-300 bg-gray-100 text-gray-500"
                      : s.visibility === "all"
                        ? "border-gray-300 text-gray-700"
                        : "border-amber-300 bg-amber-50 text-amber-800"
                  }`}
                >
                  {s.is_excluded
                    ? `除外中・${reasonLabel(s.exclude_reason)}`
                    : s.visibility === "all"
                      ? "全員"
                      : "マネージャーのみ"}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
