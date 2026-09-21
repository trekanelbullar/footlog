"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { RunOutcome, RunStep } from "@/lib/worker-types";

const STEP_LABEL: Record<RunStep, string> = {
  extracting: "抽出中",
  investigating: "調査中",
  assembling: "組み立て中",
  checking: "検査中",
  notifying: "通知中",
  done: "完了",
};

const OUTCOME_LABEL: Record<RunOutcome, string> = {
  report_created: "新しいレポートができました。",
  no_new_events: "新しい進捗はありませんでした。",
  locked: "他の実行が進行中のため、今回はスキップされました。",
  cost_limited: "本日のコスト上限に達したため、実行できませんでした。",
  failed: "実行に失敗しました。",
};

export default function RunPanel({ pid }: { pid: string }) {
  const router = useRouter();
  const [status, setStatus] = useState<"idle" | "running" | "done">("idle");
  const [step, setStep] = useState<RunStep | null>(null);
  const [outcome, setOutcome] = useState<RunOutcome | null>(null);
  const [versionNo, setVersionNo] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  function stopPolling() {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  async function pollOnce(runId: string) {
    try {
      const res = await fetch(`/api/runs/${runId}`);
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "状態の取得に失敗しました。");
        stopPolling();
        return;
      }
      setStep(data.step);
      if (data.status === "done" || data.status === "failed") {
        setOutcome(data.outcome ?? null);
        setVersionNo(data.version_no ?? null);
        setStatus("done");
        stopPolling();
        router.refresh();
      }
    } catch {
      setError("通信に失敗しました。");
      stopPolling();
    }
  }

  async function handleRunNow() {
    setError(null);
    setOutcome(null);
    setVersionNo(null);
    setStatus("running");
    try {
      const createRes = await fetch(`/api/projects/${pid}/runs`, { method: "POST" });
      const createData = await createRes.json();
      if (!createRes.ok) {
        setError(createData.message ?? "実行の作成に失敗しました。");
        setStatus("idle");
        return;
      }
      const runId = createData.run_id as string;

      // design.md §5.1：ブラウザは実行のリクエストの応答を待たず、2秒ごとの問い合わせで完了を知る。
      fetch(`/api/runs/${runId}/execute`, { method: "POST" }).catch(() => {});

      timerRef.current = setInterval(() => {
        void pollOnce(runId);
      }, 2000);
      void pollOnce(runId);
    } catch {
      setError("通信に失敗しました。");
      setStatus("idle");
    }
  }

  return (
    <section className="rounded border bg-white p-4">
      <h2 className="mb-3 text-lg font-semibold">今すぐ確認</h2>
      {error && <p className="mb-2 rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      <button
        type="button"
        onClick={handleRunNow}
        disabled={status === "running"}
        className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {status === "running" ? "実行中…" : "今すぐ確認する"}
      </button>
      {status === "running" && step && (
        <p className="mt-3 text-sm text-gray-600">状態：{STEP_LABEL[step]}</p>
      )}
      {status === "done" && outcome && (
        <div className="mt-3 text-sm">
          <p>{OUTCOME_LABEL[outcome]}</p>
          {versionNo && (
            <a href={`/projects/${pid}/reports/${versionNo}`} className="text-blue-600 hover:underline">
              版{versionNo}のレポートを見る
            </a>
          )}
        </div>
      )}
    </section>
  );
}
