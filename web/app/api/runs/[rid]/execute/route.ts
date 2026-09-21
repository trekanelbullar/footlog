import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w15ExecuteRun } from "@/lib/worker";

type Params = { params: Promise<{ rid: string }> };

// W15: POST /internal/runs/{rid}/execute
// 画面側はこのリクエストの応答を待たずに、W16 のポーリングで完了を知る（design.md §5.1）。
export async function POST(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { rid } = await params;
    const session = await getSession();
    return w15ExecuteRun(session, rid);
  });
}
