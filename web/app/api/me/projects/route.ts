import { getSession } from "@/lib/auth";
import { handle } from "@/lib/api-helpers";
import { w1ListMyProjects } from "@/lib/worker";

// W1: GET /internal/me/projects
export async function GET() {
  return handle(async () => {
    const session = await getSession();
    return w1ListMyProjects(session);
  });
}
