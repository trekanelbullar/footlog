import { getSession } from "@/lib/auth";
import { handle } from "@/lib/api-helpers";
import { w22GetQuestion } from "@/lib/worker";

type Params = { params: Promise<{ qid: string }> };

// W22: GET /internal/questions/{qid}
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { qid } = await params;
    const session = await getSession();
    return w22GetQuestion(session, qid);
  });
}
