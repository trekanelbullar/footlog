import Link from "next/link";
import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { w18ListReports, w19GetReport, w1ListMyProjects } from "@/lib/worker";
import { pickHeadline } from "@/lib/article";
import { formatJst } from "@/lib/format";
import CreateProjectForm from "@/components/CreateProjectForm";
import type { MyProjectSummary, ReportDetail } from "@/lib/worker-types";

const ROLE_LABEL: Record<string, string> = {
  manager: "マネージャー",
  member: "メンバー",
};

/** プロジェクトごとの最新の記事（自分が読める、非公開になっていない最新の版）。無ければ null。 */
async function latestReport(
  session: NonNullable<Awaited<ReturnType<typeof getSession>>>,
  project: MyProjectSummary,
): Promise<ReportDetail | null> {
  if (!project.latest_version_no) return null;
  try {
    const reports = await w18ListReports(session, project.project_id);
    const latest = reports
      .filter((r) => !r.withheld)
      .sort((a, b) => b.version_no - a.version_no)[0];
    return latest
      ? await w19GetReport(session, project.project_id, latest.version_no)
      : null;
  } catch {
    return null; // 一覧は、記事が読めなくても出す
  }
}

function Figure({
  value,
  label,
  accent,
}: {
  value: string;
  label: string;
  accent?: boolean;
}) {
  return (
    <div className="border-t border-ink pt-2">
      <p
        className={`font-serif-jp text-4xl font-black leading-none md:text-5xl ${accent ? "text-amber" : "text-ink"}`}
      >
        {value}
      </p>
      <p className="mt-2 text-[11px] font-bold tracking-[0.2em] text-[#6b655a]">
        {label}
      </p>
    </div>
  );
}

export default async function ProjectsPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const projects = await w1ListMyProjects(session);
  const latest = await Promise.all(
    projects.map((p) => latestReport(session, p)),
  );

  return (
    <div className="relative left-1/2 -my-6 min-h-[calc(100dvh-3rem)] w-screen -translate-x-1/2 bg-paper font-sans-jp text-ink">
      <section className="border-b-2 border-ink px-5 pt-14 pb-12 md:px-12 md:pt-20">
        <p className="text-[11px] font-bold tracking-[0.25em] text-accent">
          DECISION LOG
        </p>
        <h1 className="mt-5 max-w-3xl font-serif-jp text-[2.1rem] font-black leading-[1.25] md:text-6xl md:leading-[1.15]">
          AIとの会話から、決めたことと、その理由を記事にする。
        </h1>
      </section>

      {projects.length === 0 ? (
        <p className="px-5 py-12 text-sm text-[#6b655a] md:px-12">
          まだ参加しているプロジェクトがありません。
        </p>
      ) : (
        projects.map((p, i) => {
          const report = latest[i];
          const headline = report
            ? pickHeadline(report.timeline, new Set())
            : null;
          const decisions = report
            ? report.timeline.filter((t) => t.kind === "decision").length
            : 0;
          const unverified = report?.flags.unverified_ai_count ?? 0;
          return (
            <section
              key={p.project_id}
              className="border-b border-ink px-5 py-12 md:px-12"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-[11px] font-bold tracking-[0.25em] text-accent">
                  {p.name}　／　{ROLE_LABEL[p.role] ?? p.role}
                </p>
                <Link
                  href={`/projects/${p.project_id}`}
                  className="text-xs underline underline-offset-2"
                >
                  資料と実行
                </Link>
              </div>

              {report ? (
                <Link
                  href={`/projects/${p.project_id}/reports/${report.version_no}`}
                  className="group mt-5 block"
                >
                  <p className="text-[11px] font-bold tracking-[0.2em] text-[#6b655a]">
                    最新の記事　第{report.version_no}版
                  </p>
                  <h2 className="mt-2 max-w-3xl font-serif-jp text-2xl font-black leading-snug group-hover:underline md:text-4xl">
                    {headline ? headline.summary : `${p.name} の記録`}
                  </h2>
                </Link>
              ) : (
                <p className="mt-5 text-sm text-[#6b655a]">
                  まだ記事はありません。資料を登録して「今すぐ確認」を押してください。
                </p>
              )}

              <div className="mt-10 grid grid-cols-3 gap-4 md:max-w-2xl md:gap-8">
                <Figure value={String(decisions)} label="決定" />
                <Figure
                  value={String(unverified)}
                  label="未確認"
                  accent={unverified > 0}
                />
                <Figure
                  value={
                    report ? formatJst(report.generated_at).slice(5, 10) : "—"
                  }
                  label={
                    report
                      ? `最終更新 ${formatJst(report.generated_at).slice(11)}`
                      : "最終更新"
                  }
                />
              </div>
            </section>
          );
        })
      )}

      <details className="px-5 py-8 md:px-12">
        <summary className="cursor-pointer text-xs font-bold tracking-[0.2em] text-[#6b655a]">
          新しいプロジェクトを作成
        </summary>
        <div className="mt-4 max-w-xl">
          <CreateProjectForm />
        </div>
      </details>
    </div>
  );
}
