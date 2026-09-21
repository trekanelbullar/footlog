import Link from "next/link";
import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { w1ListMyProjects } from "@/lib/worker";
import CreateProjectForm from "@/components/CreateProjectForm";

const STATUS_LABEL: Record<string, string> = { active: "進行中", completed: "完了" };
const ROLE_LABEL: Record<string, string> = { manager: "マネージャー", member: "メンバー" };

export default async function ProjectsPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const projects = await w1ListMyProjects(session);

  return (
    <div className="flex flex-col gap-8">
      <section>
        <h1 className="mb-4 text-xl font-semibold">プロジェクト一覧</h1>
        {projects.length === 0 ? (
          <p className="text-sm text-gray-500">まだ参加しているプロジェクトがありません。</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {projects.map((p) => (
              <li key={p.project_id} className="rounded border bg-white p-4 hover:bg-gray-50">
                <Link href={`/projects/${p.project_id}`} className="flex items-center justify-between">
                  <span className="font-medium">{p.name}</span>
                  <span className="flex items-center gap-3 text-sm text-gray-500">
                    <span>{ROLE_LABEL[p.role] ?? p.role}</span>
                    <span>{STATUS_LABEL[p.status] ?? p.status}</span>
                    <span>
                      {p.latest_version_no ? `版${p.latest_version_no}` : "レポート未作成"}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-4 text-lg font-semibold">新しいプロジェクトを作成</h2>
        <CreateProjectForm />
      </section>
    </div>
  );
}
