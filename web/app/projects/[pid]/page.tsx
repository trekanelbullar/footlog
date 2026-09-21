import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w10ListSources, w3GetProject } from "@/lib/worker";
import ReportTab from "@/components/ReportTab";

/** プロジェクトを開いた画面＝レポートタブ（最新の版）。 */
export default async function ProjectDetailPage({
  params,
}: {
  params: Promise<{ pid: string }>;
}) {
  const { pid } = await params;
  const session = await getSession();
  if (!session) redirect("/login");

  const [detail, sources] = await loadOrNotFound(() =>
    Promise.all([w3GetProject(session, pid), w10ListSources(session, pid)]),
  );
  return (
    <ReportTab
      session={session}
      pid={pid}
      projectName={detail.project.name}
      versionNo={null}
      hasSources={sources.length > 0}
    />
  );
}
