import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w8CreateConversationSource } from "@/lib/worker";
import type { CreateConversationSourceInput } from "@/lib/worker-types";

type Params = { params: Promise<{ pid: string }> };

// W8: POST /internal/projects/{pid}/sources/conversation
export async function POST(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    const input = (await req.json()) as CreateConversationSourceInput;
    return w8CreateConversationSource(session, pid, input);
  });
}
