import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w10ListSources, w3GetProject } from "@/lib/worker";
import { ProjectActions, ProjectTabs } from "@/components/ProjectChrome";
import CostLimitedBanner from "@/components/CostLimitedBanner";

const ROLE_LABEL: Record<string, string> = {
  manager: "マネージャー",
  member: "メンバー",
};

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

  const [detail, sources] = await loadOrNotFound(() =>
    Promise.all([w3GetProject(session, pid), w10ListSources(session, pid)]),
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="border-b border-gray-200">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-xl font-semibold text-gray-900">
                {detail.project.name}
              </h1>
              <span className="hidden shrink-0 rounded-full border border-gray-300 px-2 py-0.5 text-xs text-gray-600 sm:inline">
                {ROLE_LABEL[detail.my_role] ?? detail.my_role}
              </span>
            </div>
            <p className="mt-1 line-clamp-1 text-sm text-gray-500">
              {detail.project.goal_description}
            </p>
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
  );
}
