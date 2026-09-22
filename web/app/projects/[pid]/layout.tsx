import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import {
  w10ListSources,
  w18ListReports,
  w1ListMyProjects,
  w3GetProject,
} from "@/lib/worker";
import { ProjectActions, ProjectTabs } from "@/components/ProjectChrome";
import ProjectTree, { MobileTreeButton } from "@/components/ProjectTree";
import CostLimitedBanner from "@/components/CostLimitedBanner";

const ROLE_LABEL: Record<string, string> = {
  manager: "マネージャー",
  member: "メンバー",
};

/**
 * プロジェクト内の骨格（design/ui-spec-3col.md、addenda AD-14）：左にツリー、中央の上に操作帯とタブ。
 * 右の根拠パネルはレポートタブの中（ReportViewer）が持つ。3カラムの幅を取るため、
 * 共通の本文の幅（max-w-6xl）から外に広げる（transform は fixed の子の基準を変えるので、左の余白で広げる）。
 */
export default async function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ pid: string }>;
}) {
  const { pid } = await params;
  const session = await getSession();
  if (!session) redirect("/login");

  const [detail, sources, reports, projects] = await loadOrNotFound(() =>
    Promise.all([
      w3GetProject(session, pid),
      w10ListSources(session, pid),
      w18ListReports(session, pid),
      w1ListMyProjects(session),
    ]),
  );
  const tree = { pid, projects, reports };

  return (
    <div className="-my-6 ml-[calc(50%-min(50vw,720px))] flex min-h-[calc(100vh-3.5rem)] w-[min(100vw,1440px)] bg-[#f7f5f1]">
      <ProjectTree {...tree} />
      <div className="flex min-w-0 flex-1 flex-col gap-6 px-4 py-6 lg:px-8">
        <div className="border-b border-gray-200">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-1">
              <MobileTreeButton {...tree} />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="truncate text-base font-semibold text-gray-900">
                    {detail.project.name}
                  </p>
                  <span className="hidden shrink-0 rounded-full border border-gray-300 px-2 py-0.5 text-xs text-gray-600 sm:inline">
                    {ROLE_LABEL[detail.my_role] ?? detail.my_role}
                  </span>
                </div>
                <p className="line-clamp-1 text-xs text-gray-500">
                  {detail.project.goal_description}
                </p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <ProjectActions pid={pid} hasSources={sources.length > 0} />
            </div>
          </div>
          <div className="mt-4">
            <ProjectTabs pid={pid} />
          </div>
        </div>
        {detail.cost_limited_today && <CostLimitedBanner />}
        {children}
      </div>
    </div>
  );
}
