"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Visibility } from "@/lib/worker-types";
import Button from "@/components/ui/Button";
import { FileField, SelectField } from "@/components/ui/Field";
import { InlineError, InlineNotice } from "@/components/ui/Feedback";

export default function AddFileForm({
  pid,
  onDone,
}: {
  pid: string;
  onDone?: (message: string) => void;
}) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [visibility, setVisibility] = useState<Visibility>("all");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setWarning(null);
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
      const done = `アップロードしました（${data.label_prefix}、版${data.version_no}、区切り${data.segment_count}件）。`;
      setMessage(done);
      setFile(null);
      router.refresh();
      // 抽出の失敗・打ち切りは、その場に残して知らせる（モーダルを閉じない）
      if (data.extraction_failed) {
        setWarning(
          `${data.label_prefix} として登録しましたが、文字を抽出できませんでした。ファイル名だけを記録し、レポートには載りません。`
        );
        return;
      }
      if (data.truncated) {
        setWarning(
          `区切りが多すぎたため、先頭から${data.segment_limit ?? 2000}件で打ち切りました（${data.label_prefix}）。`
        );
        return;
      }
      onDone?.(done);
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <InlineError>{error}</InlineError>
      {warning && <InlineNotice>{warning}</InlineNotice>}
      {message && !onDone && !warning && <p className="text-sm text-gray-600">{message}</p>}
      <FileField
        label="ファイル"
        help="txt・md・csv・py・ts・js・json・xlsx・docx・pptx・pdf（10MBまで）。古い形式（xls・doc・ppt）は新しい形式で保存し直してください。"
        accept=".txt,.md,.csv,.py,.ts,.js,.json,.xlsx,.docx,.pptx,.pdf"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
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
          {submitting ? "アップロードしています…" : "アップロードする"}
        </Button>
      </div>
    </form>
  );
}
