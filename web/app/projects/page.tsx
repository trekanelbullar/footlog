import Link from "next/link";
import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { w1ListMyProjects } from "@/lib/worker";
import NewProjectButton from "@/components/NewProjectButton";
import { EmptyState } from "@/components/ui/Feedback";

const STATUS_LABEL: Record<string, string> = {
  active: "進行中",
  completed: "完了",
};
const ROLE_LABEL: Record<string, string> = {
  manager: "マネージャー",
  member: "メンバー",
};

export default async function ProjectsPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const projects = await w1ListMyProjects(session);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-3 border-b border-gray-200 pb-4">
        <h1 className="text-xl font-semibold text-gray-900">プロジェクト</h1>
        <NewProjectButton />
      </div>

      {projects.length === 0 ? (
        <EmptyState
          title="まだ参加しているプロジェクトがありません"
          body="右上の「＋ 新規プロジェクト」から作成するか、マネージャーに招待してもらってください。"
        />
      ) : (
        <ul className="divide-y divide-gray-200 rounded-md border border-gray-200">
          {projects.map((p) => (
            <li key={p.project_id}>
              <Link
                href={`/projects/${p.project_id}`}
                className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-gray-50 focus-visible:outline-2 focus-visible:outline-gray-900"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-gray-900">
                    {p.name}
                  </span>
                  <span className="text-xs text-gray-500">
                    {ROLE_LABEL[p.role] ?? p.role} ・{" "}
                    {p.latest_version_no
                      ? `最新 第${p.latest_version_no}版`
                      : "レポート未作成"}
                  </span>
                </span>
                <span
                  className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${
                    p.status === "active"
                      ? "border-gray-300 text-gray-700"
                      : "border-gray-200 text-gray-400"
                  }`}
                >
                  {STATUS_LABEL[p.status] ?? p.status}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
