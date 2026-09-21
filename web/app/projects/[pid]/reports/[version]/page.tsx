import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w18ListReports, w19GetReport, w3GetProject } from "@/lib/worker";
import ReportViewer from "@/components/ReportViewer";
import VersionSwitcher from "@/components/VersionSwitcher";

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
    ])
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{detail.project.name} のレポート</h1>
        <VersionSwitcher pid={pid} reports={reports} currentVersion={versionNo} />
      </div>
      {/* 版が変わったら内部状態（選択中の根拠パネルなど）を確実にリセットするため key で再マウントする */}
      <ReportViewer key={report.version_no} report={report} />
    </div>
  );
}
