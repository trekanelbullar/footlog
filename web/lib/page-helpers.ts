import "server-only";
import { notFound } from "next/navigation";
import { WorkerApiError } from "@/lib/worker-types";

/**
 * Server Component から worker.ts を呼ぶときの共通処理。
 * 404（存在しない・権限が無い）は Next の notFound() に変換し、それ以外はそのまま投げる。
 */
export async function loadOrNotFound<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof WorkerApiError && e.status === 404) {
      notFound();
    }
    throw e;
  }
}
