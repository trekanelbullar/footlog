"use client";

import { SelectField } from "@/components/ui/Field";
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
    <SelectField
      label="版"
      hideLabel
      className="w-full sm:w-56"
      value={currentVersion}
      onChange={(e) =>
        router.push(`/projects/${pid}/reports/${e.target.value}`)
      }
    >
      {[...reports]
        .sort((a, b) => b.version_no - a.version_no)
        .map((r) => (
          <option key={r.version_no} value={r.version_no}>
            第{r.version_no}版{r.withheld ? "（表示不可）" : ""}
            {r.audience === "managers" ? "・管理者向け" : ""}
          </option>
        ))}
    </SelectField>
  );
}
