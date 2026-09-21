import { getSession } from "@/lib/auth";
import { handle } from "@/lib/api-helpers";
import { w18ListReports } from "@/lib/worker";

type Params = { params: Promise<{ pid: string }> };

// W18: GET /internal/projects/{pid}/reports
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    return w18ListReports(session, pid);
  });
}
