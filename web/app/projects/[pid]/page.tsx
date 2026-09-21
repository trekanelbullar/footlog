import Link from "next/link";
import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w3GetProject, w10ListSources, w18ListReports } from "@/lib/worker";
import SourceList from "@/components/SourceList";
import AddConversationForm from "@/components/AddConversationForm";
import AddFileForm from "@/components/AddFileForm";
import RunPanel from "@/components/RunPanel";
import type { ExcludeReason } from "@/lib/worker-types";

const REASON_LABEL: Record<ExcludeReason, string> = {
  private: "個人的な内容",
  confidential: "機密情報",
  irrelevant: "無関係な内容",
  other: "その他",
};

export default async function ProjectDetailPage({
  params,
}: {
  params: Promise<{ pid: string }>;
}) {
  const { pid } = await params;
  const session = await getSession();
  if (!session) redirect("/login");

  const [detail, sources, reports] = await loadOrNotFound(() =>
    Promise.all([w3GetProject(session, pid), w10ListSources(session, pid), w18ListReports(session, pid)])
  );

  const latestReport = reports.length ? reports[reports.length - 1] : null;

  return (
    <div className="flex flex-col gap-8">
      <section className="rounded border bg-white p-4">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-semibold">{detail.project.name}</h1>
            <p className="mt-1 text-sm text-gray-600">{detail.project.goal_description}</p>
          </div>
          <Link
            href={`/projects/${pid}/members`}
            className="whitespace-nowrap text-sm text-blue-600 hover:underline"
          >
            メンバー管理
          </Link>
        </div>

        {detail.excluded_summary && (
          <div className="mt-4 rounded bg-amber-50 p-3 text-sm text-amber-800">
            <p className="font-medium">除外されたソース：{detail.excluded_summary.count}件</p>
            <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              {(Object.keys(REASON_LABEL) as Array<keyof typeof REASON_LABEL>).map((reason) => (
                <li key={reason}>
                  {REASON_LABEL[reason]}：{detail.excluded_summary!.by_reason[reason]}件
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="rounded border bg-white p-4">
        <h2 className="mb-3 text-lg font-semibold">レポート</h2>
        {latestReport ? (
          <Link
            href={`/projects/${pid}/reports/${latestReport.version_no}`}
            className="text-blue-600 hover:underline"
          >
            最新の版（版{latestReport.version_no}）を見る
          </Link>
        ) : (
          <p className="text-sm text-gray-500">まだレポートがありません。「今すぐ確認」を実行してください。</p>
        )}
        {reports.length > 1 && (
          <p className="mt-2 text-sm text-gray-500">
            版一覧：
            {reports.map((r) => (
              <Link
                key={r.version_no}
                href={`/projects/${pid}/reports/${r.version_no}`}
                className="ml-2 text-blue-600 hover:underline"
              >
                版{r.version_no}
              </Link>
            ))}
          </p>
        )}
      </section>

      <RunPanel pid={pid} />

      <section className="rounded border bg-white p-4">
        <h2 className="mb-3 text-lg font-semibold">ソース一覧</h2>
        <SourceList initialSources={sources} myUserId={session.userId} />
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded border bg-white p-4">
          <h2 className="mb-3 text-lg font-semibold">会話を貼り付ける</h2>
          <AddConversationForm pid={pid} />
        </div>
        <div className="rounded border bg-white p-4">
          <h2 className="mb-3 text-lg font-semibold">ファイルをアップロードする</h2>
          <AddFileForm pid={pid} />
        </div>
      </section>
    </div>
  );
}
