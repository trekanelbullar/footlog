import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w10ListSources, w3GetProject } from "@/lib/worker";
import ReportTab from "@/components/ReportTab";

export default async function ReportPage({
  params,
}: {
  params: Promise<{ pid: string; version: string }>;
}) {
  const { pid, version } = await params;
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
      versionNo={Number.parseInt(version, 10)}
      hasSources={sources.length > 0}
      running={detail.latest_run?.status === "running"}
    />
  );
}
