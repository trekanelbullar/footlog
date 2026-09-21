import { getSession } from "@/lib/auth";
import { handle } from "@/lib/api-helpers";
import { w19GetReport } from "@/lib/worker";
import { WorkerApiError } from "@/lib/worker-types";

type Params = { params: Promise<{ pid: string; version_no: string }> };

// W19: GET /internal/projects/{pid}/reports/{version_no}
// クエリ文字列（?audience= など）は一切見ない・転送しない（design.md §2.4 B-X3）。
export async function GET(_req: Request, { params }: Params) {
  return handle(async () => {
    const { pid, version_no } = await params;
    const versionNo = Number.parseInt(version_no, 10);
    if (!Number.isInteger(versionNo)) {
      throw new WorkerApiError(400, { error: "invalid_input", message: "version_no が不正です。" });
    }
    const session = await getSession();
    return w19GetReport(session, pid, versionNo);
  });
}
