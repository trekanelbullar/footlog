import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w23AnswerQuestion } from "@/lib/worker";
import type { AnswerQuestionInput } from "@/lib/worker-types";

type Params = { params: Promise<{ qid: string }> };

// W23: POST /internal/questions/{qid}/answer
export async function POST(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { qid } = await params;
    const session = await getSession();
    const input = (await req.json()) as AnswerQuestionInput;
    return w23AnswerQuestion(session, qid, input);
  });
}
