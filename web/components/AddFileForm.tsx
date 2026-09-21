"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Visibility } from "@/lib/worker-types";

export default function AddFileForm({ pid }: { pid: string }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [visibility, setVisibility] = useState<Visibility>("all");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    if (!file) {
      setError("ファイルを選択してください。");
      return;
    }
    setSubmitting(true);
    try {
      const formData = new FormData();
      formData.set("file", file);
      formData.set("visibility", visibility);
      const res = await fetch(`/api/projects/${pid}/sources/file`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "アップロードに失敗しました。");
        return;
      }
      setMessage(
        `アップロードしました（${data.label_prefix}、版${data.version_no}、区切り${data.segment_count}件）。`
      );
      setFile(null);
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      {message && <p className="rounded bg-green-50 p-2 text-sm text-green-700">{message}</p>}
      <input
        type="file"
        className="text-sm"
        accept=".txt,.md,.csv,.py,.ts,.js,.json,.xlsx,.pdf"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        required
      />
      <label className="flex items-center gap-2 text-sm">
        公開範囲
        <select
          className="rounded border px-2 py-1"
          value={visibility}
          onChange={(e) => setVisibility(e.target.value as Visibility)}
        >
          <option value="all">全員</option>
          <option value="managers_only">マネージャーのみ</option>
        </select>
      </label>
      <button
        type="submit"
        disabled={submitting}
        className="w-fit rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
      >
        アップロードする
      </button>
    </form>
  );
}
