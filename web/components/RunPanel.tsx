"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { RunOutcome, RunStep } from "@/lib/worker-types";

// 実行の工程（worker の step）を、記事を組む4つの工程に読み替える。
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

  // 実行中は、記事を組んでいる途中のように4つの工程で進み具合を見せる。
  const stageIndex = step ? STAGE_OF_STEP[step] : 0;

  return (
    <section className="border-y-2 border-ink bg-paper px-5 py-6 font-sans-jp text-ink">
      <p className="text-[11px] font-bold tracking-[0.25em] text-accent">
        PRESS
      </p>
      <h2 className="mt-1 mb-4 font-serif-jp text-xl font-black">今すぐ確認</h2>
      {error && (
        <p className="mb-3 border-l-4 border-accent pl-3 text-sm text-accent">
          {error}
        </p>
      )}
      <button
        type="button"
        onClick={handleRunNow}
        disabled={status === "running"}
        className="border-2 border-ink bg-ink px-5 py-2 text-sm font-bold text-paper hover:bg-paper hover:text-ink disabled:opacity-50"
      >
        {status === "running" ? "組版中…" : "今すぐ確認する"}
      </button>
      {status === "running" && (
        <ol className="mt-5 grid grid-cols-4 border-t border-ink text-xs">
          {STAGES.map((label, i) => (
            <li
              key={label}
              className={`border-t-4 pt-2 pr-2 ${
                i < stageIndex
                  ? "border-ink text-ink"
                  : i === stageIndex
                    ? "border-accent font-bold text-accent"
                    : "border-transparent text-[#9a9486]"
              }`}
            >
              {String(i + 1).padStart(2, "0")} {label}
              {i === stageIndex && <span className="ml-1">…</span>}
            </li>
          ))}
        </ol>
      )}
      {status === "done" && outcome && (
        <div className="mt-4 text-sm">
          <p>{OUTCOME_LABEL[outcome]}</p>
          {versionNo && (
            <a
              href={`/projects/${pid}/reports/${versionNo}`}
              className="font-bold text-accent underline underline-offset-2"
            >
              第{versionNo}版の記事を読む
            </a>
          )}
        </div>
      )}
    </section>
  );
}
