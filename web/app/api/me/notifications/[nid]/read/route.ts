import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w21MarkNotificationRead } from "@/lib/worker";

type Params = { params: Promise<{ nid: string }> };

// W21: POST /internal/me/notifications/{nid}/read
export async function POST(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { nid } = await params;
    const session = await getSession();
    return w21MarkNotificationRead(session, nid);
  });
}
