import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w11UpdateVisibility } from "@/lib/worker";
import type { UpdateVisibilityInput } from "@/lib/worker-types";

type Params = { params: Promise<{ sid: string }> };

// W11: PATCH /internal/sources/{sid}/visibility
export async function PATCH(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { sid } = await params;
    const session = await getSession();
    const input = (await req.json()) as UpdateVisibilityInput;
    return w11UpdateVisibility(session, sid, input);
  });
}
