import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { w20ListNotifications } from "@/lib/worker";
import NotificationList from "@/components/NotificationList";

export default async function NotificationsPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const notifications = await w20ListNotifications(session);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">通知</h1>
      <NotificationList initialNotifications={notifications} />
    </div>
  );
}
