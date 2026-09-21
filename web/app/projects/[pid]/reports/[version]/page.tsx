import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w18ListReports, w19GetReport, w3GetProject } from "@/lib/worker";
import { newEventNos } from "@/lib/article";
import ReportViewer from "@/components/ReportViewer";
import VersionSwitcher from "@/components/VersionSwitcher";
import CostLimitedBanner from "@/components/CostLimitedBanner";
import EventSearchPanel from "@/components/EventSearchPanel";
import type { TimelineItem } from "@/lib/worker-types";

export default async function ReportPage({
  params,
}: {
  params: Promise<{ pid: string; version: string }>;
}) {
  const { pid, version } = await params;
  const session = await getSession();
  if (!session) redirect("/login");

  const versionNo = Number.parseInt(version, 10);
  const [detail, reports, report] = await loadOrNotFound(() =>
    Promise.all([
      w3GetProject(session, pid),
      w18ListReports(session, pid),
      w19GetReport(session, pid, versionNo),
    ]),
  );

  // NEW の印：1つ前の見られる版の時系列に無い出来事。前の版が無ければ付けない。
  const previous = reports
    .filter((r) => r.version_no < versionNo && !r.withheld)
    .sort((a, b) => b.version_no - a.version_no)[0];
  let previousTimeline: TimelineItem[] | null = null;
  if (previous && !report.withheld) {
    try {
      previousTimeline = (await w19GetReport(session, pid, previous.version_no))
        .timeline;
    } catch {
      previousTimeline = null; // 前の版が読めなくても、記事は NEW なしで出す
    }
  }
  const fresh = [...newEventNos(report.timeline, previousTimeline)];

  return (
    <div className="relative left-1/2 -my-6 flex w-screen -translate-x-1/2 flex-col bg-paper">
      {detail.cost_limited_today && (
        <div className="px-4 pt-4">
          <CostLimitedBanner />
        </div>
      )}

      {/* 版が変わったら内部状態を確実にリセットするため key で再マウントする */}
      <ReportViewer
        key={report.version_no}
        report={report}
        projectName={detail.project.name}
        newEventNos={fresh}
        toolbar={
          <VersionSwitcher
            pid={pid}
            reports={reports}
            currentVersion={versionNo}
          />
        }
      />

      <div className="bg-paper px-5 pb-12 md:px-12">
        <EventSearchPanel pid={pid} />
      </div>
    </div>
  );
}
