import { getSession } from "@/lib/auth";
import { handle } from "@/lib/api-helpers";
import { w10ListSources } from "@/lib/worker";

type Params = { params: Promise<{ pid: string }> };

// W10: GET /internal/projects/{pid}/sources
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    return w10ListSources(session, pid);
  });
}
