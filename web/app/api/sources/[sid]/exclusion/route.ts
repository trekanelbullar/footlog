import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w12UpdateExclusion } from "@/lib/worker";
import type { UpdateExclusionInput } from "@/lib/worker-types";

type Params = { params: Promise<{ sid: string }> };

// W12: PATCH /internal/sources/{sid}/exclusion
export async function PATCH(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { sid } = await params;
    const session = await getSession();
    const input = (await req.json()) as UpdateExclusionInput;
    return w12UpdateExclusion(session, sid, input);
  });
}
