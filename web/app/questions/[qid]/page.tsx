import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w22GetQuestion } from "@/lib/worker";
import QuestionAnswerForm from "@/components/QuestionAnswerForm";

export default async function QuestionPage({ params }: { params: Promise<{ qid: string }> }) {
  const { qid } = await params;
  const session = await getSession();
  if (!session) redirect("/login");

  const question = await loadOrNotFound(() => w22GetQuestion(session, qid));

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-4">
      <h1 className="text-xl font-semibold">質問への回答</h1>
      <div className="rounded border bg-white p-4">
        <p className="mb-2 text-xs text-gray-500">関連する出来事：{question.related_event_summary}</p>
        <p className="whitespace-pre-wrap text-sm">{question.question}</p>
      </div>
      {question.status === "open" ? (
        <QuestionAnswerForm qid={qid} />
      ) : (
        <p className="rounded bg-gray-100 p-3 text-sm text-gray-600">
          この質問はすでに{question.status === "answered" ? "回答済み" : "期限切れ"}です。
        </p>
      )}
    </div>
  );
}
