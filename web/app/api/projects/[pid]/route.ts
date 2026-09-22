import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w3GetProject, w4UpdateProject } from "@/lib/worker";
import type { UpdateProjectInput } from "@/lib/worker-types";

type Params = { params: Promise<{ pid: string }> };

// W3: GET /internal/projects/{pid}
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    return w3GetProject(session, pid);
  });
}

// W4: PATCH /internal/projects/{pid}
export async function PATCH(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    const input = (await req.json()) as UpdateProjectInput;
    return w4UpdateProject(session, pid, input);
  });
}
