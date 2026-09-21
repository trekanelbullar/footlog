import { getSession } from "@/lib/auth";
import { handle } from "@/lib/api-helpers";
import { w25SearchEvents } from "@/lib/worker";

type Params = { params: Promise<{ pid: string }> };

// W25: GET /internal/projects/{pid}/events/search?q=...（AD-12）
// GET のため CSRF の確認は不要（design.md §2.7）。セッションの確認は w25SearchEvents の
// requireSession で行う（無ければ 401）。
export async function GET(req: Request, { params }: Params) {
  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    const q = new URL(req.url).searchParams.get("q") ?? "";
    return w25SearchEvents(session, pid, q);
  });
}
