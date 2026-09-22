import { getSession } from "@/lib/auth";
import { handle } from "@/lib/api-helpers";
import { w16GetRun } from "@/lib/worker";

type Params = { params: Promise<{ rid: string }> };

// W16: GET /internal/runs/{rid}
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { rid } = await params;
    const session = await getSession();
    return w16GetRun(session, rid);
  });
}
