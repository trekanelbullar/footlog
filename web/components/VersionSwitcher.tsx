"use client";

import { useRouter } from "next/navigation";
import type { ReportSummary } from "@/lib/worker-types";

export default function VersionSwitcher({
  pid,
  reports,
  currentVersion,
}: {
  pid: string;
  reports: ReportSummary[];
  currentVersion: number;
}) {
  const router = useRouter();

  return (
    <label className="flex items-center gap-2 font-sans-jp text-xs tracking-wide text-[#6b655a]">
      版
      <select
        className="border-b border-ink bg-transparent px-1 py-0.5 text-ink"
        value={currentVersion}
        onChange={(e) => router.push(`/projects/${pid}/reports/${e.target.value}`)}
      >
        {reports.map((r) => (
          <option key={r.version_no} value={r.version_no}>
            版{r.version_no}
            {r.withheld ? "（表示不可）" : ""}
            {r.audience === "managers" ? "・管理者向け" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
