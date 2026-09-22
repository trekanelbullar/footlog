import { getSession } from "@/lib/auth";
import { checkCsrf } from "@/lib/csrf";
import { handle } from "@/lib/api-helpers";
import { w9CreateFileSource } from "@/lib/worker";
import { WorkerApiError, type Visibility } from "@/lib/worker-types";

type Params = { params: Promise<{ pid: string }> };

// W9: POST /internal/projects/{pid}/sources/file （multipart）
export async function POST(req: Request, { params }: Params) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  return handle(async () => {
    const { pid } = await params;
    const session = await getSession();
    const formData = await req.formData();
    const file = formData.get("file");
    const visibility = formData.get("visibility");
    const recordedAt = formData.get("recorded_at");

    if (!(file instanceof File)) {
      throw new WorkerApiError(400, { error: "invalid_input", message: "file が必要です。" });
    }
    if (visibility !== "all" && visibility !== "managers_only") {
      throw new WorkerApiError(400, { error: "invalid_input", message: "visibility が不正です。" });
    }

    return w9CreateFileSource(
      session,
      pid,
      file,
      typeof recordedAt === "string" && recordedAt ? recordedAt : undefined,
      visibility as Visibility
    );
  });
}
