import Link from "next/link";
import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { w20ListNotifications } from "@/lib/worker";
import { formatJst } from "@/lib/format";
import { EmptyState } from "@/components/ui/Feedback";

/** 質問タブ：このプロジェクトで自分宛てに届いた質問（通知の一覧から絞る）。 */
export default async function QuestionsPage({
  params,
}: {
  params: Promise<{ pid: string }>;
}) {
  const { pid } = await params;
  const session = await getSession();
  if (!session) redirect("/login");

  const questions = (await w20ListNotifications(session)).filter(
    (n) => n.project_id === pid && n.kind === "question" && n.question_id,
  );

  if (questions.length === 0) {
    return (
      <EmptyState
        title="質問はありません"
        body="理由がわからない決定があると、AI が登録した人に質問を送ります。"
      />
    );
  }
  return (
    <ul className="divide-y divide-gray-200 rounded-md border border-gray-200">
      {questions.map((q) => (
        <li key={q.id}>
          <Link
            href={`/questions/${q.question_id}`}
            className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-gray-50 focus-visible:outline-2 focus-visible:outline-gray-900"
          >
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium text-gray-900">
                {q.title}
              </span>
              <span className="text-xs text-gray-500">
                {formatJst(q.created_at)}
              </span>
            </span>
            <span
              className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${
                q.read_at
                  ? "border-gray-300 text-gray-500"
                  : "border-indigo-300 bg-indigo-50 text-indigo-800"
              }`}
            >
              {q.read_at ? "既読" : "未読"}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
