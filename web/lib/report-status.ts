import type { ReportDetail } from "@/lib/worker-types";

/**
 * 版のステータス（design/ui-spec-3col.md のメタデータチップ、addenda AD-14）。
 * 検査中＝実行が running、要確認＝Judge が flagged・未確認の出来事が1件以上・誘導の疑いあり、
 * 検査済み＝それ以外。「承認済み」は今のデータに無いので作らない。
 */
export type ReportStatus = "checking" | "needs_review" | "checked";

export const STATUS_LABEL: Record<ReportStatus, string> = {
  checking: "検査中",
  needs_review: "要確認",
  checked: "検査済み",
};

export function reportStatus(
  report: ReportDetail,
  running: boolean,
): ReportStatus {
  if (running) return "checking";
  const unverified = report.flags.unverified_ai_count ?? 0;
  const injection = report.flags.suspected_injection ?? [];
  if (
    report.judge_status === "flagged" ||
    unverified > 0 ||
    injection.length > 0
  )
    return "needs_review";
  return "checked";
}
