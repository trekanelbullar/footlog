import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w14CreateRun } from "@/lib/worker";

type Params = { params: Promise<{ pid: string }> };

// W14: POST /internal/projects/{pid}/runs
export async function POST(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    return w14CreateRun(session, pid);
  });
}
