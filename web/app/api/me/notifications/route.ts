import { getSession } from "@/lib/auth";
import { handle } from "@/lib/api-helpers";
import { w20ListNotifications } from "@/lib/worker";

// W20: GET /internal/me/notifications
export async function GET() {
  return handle(async () => {
    const session = await getSession();
    return w20ListNotifications(session);
  });
}
