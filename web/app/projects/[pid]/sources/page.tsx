import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w10ListSources, w3GetProject } from "@/lib/worker";
import SourceList from "@/components/SourceList";
import { EmptyState } from "@/components/ui/Feedback";
import type { ExcludeReason } from "@/lib/worker-types";

const REASON_LABEL: Record<ExcludeReason, string> = {
  private: "個人的な内容",
  confidential: "機密情報",
  irrelevant: "無関係な内容",
  other: "その他",
};

/** 資料タブ：登録した資料の一覧（primary の「＋ 資料を追加」は見出し行の右上）。 */
export default async function SourcesPage({
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
    <div className="flex flex-col gap-4">
      {detail.excluded_summary && (
        <p className="text-sm text-gray-600">
          除外されたソース {detail.excluded_summary.count}件（
          {(Object.keys(REASON_LABEL) as ExcludeReason[])
            .map(
              (r) =>
                `${REASON_LABEL[r]} ${detail.excluded_summary!.by_reason[r] ?? 0}`,
            )
            .join("・")}
          ）
        </p>
      )}
      {sources.length === 0 ? (
        <EmptyState
          title="まだ資料がありません"
          body="右上の「＋ 資料を追加」から、会話ログやファイルを登録してください。"
        />
      ) : (
        <SourceList
          initialSources={sources}
          myUserId={session.userId}
          isManager={detail.my_role === "manager"}
        />
      )}
    </div>
  );
}
