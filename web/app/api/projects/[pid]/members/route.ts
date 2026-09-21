import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w5ListMembers, w6AddMember } from "@/lib/worker";
import type { AddMemberInput } from "@/lib/worker-types";

type Params = { params: Promise<{ pid: string }> };

// W5: GET /internal/projects/{pid}/members
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    return w5ListMembers(session, pid);
  });
}

// W6: POST /internal/projects/{pid}/members
export async function POST(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    const input = (await req.json()) as AddMemberInput;
    return w6AddMember(session, pid, input);
  });
}
