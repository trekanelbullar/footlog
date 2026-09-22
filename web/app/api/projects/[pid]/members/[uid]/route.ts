import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w7UpdateMember, w7bRemoveMember } from "@/lib/worker";
import type { UpdateMemberInput } from "@/lib/worker-types";

type Params = { params: Promise<{ pid: string; uid: string }> };

// W7: PATCH /internal/projects/{pid}/members/{uid}
export async function PATCH(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { pid, uid } = await params;
    const session = await getSession();
    const input = (await req.json()) as UpdateMemberInput;
    return w7UpdateMember(session, pid, uid, input);
  });
}

// W7b: DELETE /internal/projects/{pid}/members/{uid}
export async function DELETE(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { pid, uid } = await params;
    const session = await getSession();
    return w7bRemoveMember(session, pid, uid);
  });
}
