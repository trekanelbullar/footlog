"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { RunOutcome, RunStep } from "@/lib/worker-types";
import Button, { type ButtonVariant } from "@/components/ui/Button";
import { Toast } from "@/components/ui/Feedback";

// 実行の工程（worker の step）を、4つの工程に読み替える。
const STAGES = ["取り込み", "抽出", "組み立て", "検査"] as const;
const STAGE_OF_STEP: Record<RunStep, number> = {
  extracting: 1,
  investigating: 1,
  assembling: 2,
  checking: 3,
  notifying: 3,
  done: 3,
};

const OUTCOME_LABEL: Record<RunOutcome, string> = {
  report_created: "新しいレポートができました。",
  no_new_events: "新しい進捗はありませんでした。",
  locked: "他の実行が進行中のため、今回はスキップされました。",
  cost_limited: "本日のコスト上限に達したため、実行できませんでした。",
  failed: "実行に失敗しました。",
};

/** 「今すぐ確認」：押すと、ボタンの中に工程を出しながら実行し、終わったら新しい版を開く。 */
export default function RunPanel({
  pid,
  variant = "primary",
}: {
  pid: string;
  variant?: ButtonVariant;
}) {
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
        if (data.outcome === "report_created" && data.version_no) {
          router.push(`/projects/${pid}/reports/${data.version_no}`);
        }
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
      const createRes = await fetch(`/api/projects/${pid}/runs`, {
        method: "POST",
      });
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

  const stageIndex = step ? STAGE_OF_STEP[step] : 0;
  const failed = outcome === "failed" || outcome === "cost_limited";

  return (
    <div className="relative">
      <Button
        variant={variant}
        onClick={handleRunNow}
        disabled={status === "running"}
      >
        {status === "running" ? (
          <>
            <span className="inline-block size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
            {STAGES[stageIndex]}中…
          </>
        ) : (
          <>
            <span aria-hidden>↻</span>
            <span>
              <span className="hidden sm:inline">今すぐ</span>確認
            </span>
          </>
        )}
      </Button>
      {status === "running" && (
        <ol
          className="absolute top-full right-0 mt-1 flex w-56 gap-1"
          aria-label="進み具合"
        >
          {STAGES.map((label, i) => (
            <li key={label} className="flex-1">
              <span
                className={`block h-1 rounded ${i <= stageIndex ? "bg-gray-900" : "bg-gray-200"}`}
              />
              <span
                className={`mt-0.5 block text-[10px] ${i === stageIndex ? "font-semibold text-gray-900" : "text-gray-400"}`}
              >
                {label}
              </span>
            </li>
          ))}
        </ol>
      )}
      {(error || (status === "done" && failed)) && (
        <p
          role="alert"
          className="absolute top-full right-0 mt-1 w-64 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700"
        >
          {error ?? (outcome ? OUTCOME_LABEL[outcome] : "")}
        </p>
      )}
      <Toast
        message={
          status === "done" && outcome && !failed
            ? OUTCOME_LABEL[outcome] + (versionNo ? `（第${versionNo}版）` : "")
            : null
        }
        onDone={() => setStatus("idle")}
      />
    </div>
  );
}
