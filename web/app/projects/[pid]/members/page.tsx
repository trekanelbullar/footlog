import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadOrNotFound } from "@/lib/page-helpers";
import { w3GetProject, w5ListMembers } from "@/lib/worker";
import MembersPanel from "@/components/MembersPanel";

export default async function MembersPage({ params }: { params: Promise<{ pid: string }> }) {
  const { pid } = await params;
  const session = await getSession();
  if (!session) redirect("/login");

  const [detail, members] = await loadOrNotFound(() =>
    Promise.all([w3GetProject(session, pid), w5ListMembers(session, pid)])
  );

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold">{detail.project.name} のメンバー</h1>
      <MembersPanel
        pid={pid}
        initialMembers={members}
        myUserId={session.userId}
        myRole={detail.my_role}
      />
    </div>
  );
}
