import { NextResponse } from "next/server";
import { checkCsrf } from "@/lib/csrf";
import { signOut } from "@/lib/auth";

export async function POST(req: Request) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  await signOut();
  return NextResponse.json({ ok: true });
}
