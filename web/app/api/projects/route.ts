import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w2CreateProject } from "@/lib/worker";
import type { CreateProjectInput } from "@/lib/worker-types";

// W2: POST /internal/projects
export async function POST(req: Request) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const session = await getSession();
    const input = (await req.json()) as CreateProjectInput;
    return w2CreateProject(session, input);
  });
}
