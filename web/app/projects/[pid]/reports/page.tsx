import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w18ListReports } from "@/lib/worker";

// 版を指定しないアクセスは、最新の版へ振り替える。
export default async function ReportsIndexPage({ params }: { params: Promise<{ pid: string }> }) {
  const { pid } = await params;
  const session = await getSession();
  if (!session) redirect("/login");

  const reports = await loadOrNotFound(() => w18ListReports(session, pid));
  if (reports.length === 0) redirect(`/projects/${pid}`);

  const latest = reports[reports.length - 1];
  redirect(`/projects/${pid}/reports/${latest.version_no}`);
}
