import { getSession } from "@/lib/auth";
import { handle } from "@/lib/api-helpers";
import { w13GetDownloadUrl } from "@/lib/worker";

type Params = { params: Promise<{ sid: string }> };

// W13: GET /internal/sources/{sid}/download-url
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { sid } = await params;
    const session = await getSession();
    return w13GetDownloadUrl(session, sid);
  });
}
