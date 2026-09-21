import "server-only";
import type { Session } from "@/lib/auth";
import { isMockMode } from "@/lib/mock/mode";
import * as mock from "@/lib/mock/store";
import { WorkerApiError, type WorkerError } from "@/lib/worker-types";
import type {
  AddMemberInput,
  AddMemberOutput,
  AnswerQuestionInput,
  AnswerQuestionOutput,
  CreateConversationSourceInput,
  CreateProjectInput,
  CreateProjectOutput,
  CreateRunOutput,
  CreateSourceOutput,
  DownloadUrlOutput,
  ExecuteRunOutput,
  Member,
  MyProjectSummary,
  NotificationItem,
  ProjectDetail,
  QuestionDetail,
  RemoveMemberOutput,
  ReportDetail,
  ReportSummary,
  RunStatusOutput,
  MarkNotificationReadOutput,
  SourceSummary,
  UpdateExclusionInput,
  UpdateMemberInput,
  UpdateMemberOutput,
  UpdateProjectInput,
  UpdateProjectOutput,
  UpdateSourceOutput,
  UpdateVisibilityInput,
  Visibility,
} from "@/lib/worker-types";

/**
 * worker（Python）を呼ぶ唯一の場所。design.md §1.2・§2.7。
 * 全リクエストに X-Worker-Secret を、ユーザー操作由来のものには Authorization: Bearer <JWT> を付ける。
 *
 * モックモードの条件（WORKER_MOCK=1 かつ NODE_ENV=development）は isMockMode() の1か所だけで判定する。
 * 本番ビルドでは isMockMode() は常に false になるため、モック分岐には絶対に入らない。
 */

function mockUserIdFromToken(accessToken: string): string {
  // モックモードの accessToken は auth.ts が発行する "mock:<userId>" 形式のみ。
  return accessToken.replace(/^mock:/, "");
}

// ---- Cloud Run の IAM 認証（design.md §11）----
// worker を「認証が必要」で動かすとき、web のサービスアカウントの ID トークンを
// X-Serverless-Authorization で送る（Authorization にはユーザーの JWT が入るため）。
// WORKER_ID_TOKEN_AUDIENCE（worker の URL）が設定されているときだけ使う（手元では使わない）。
let cachedIdToken: { token: string; expiresAt: number } | null = null;

async function workerIdToken(): Promise<string | null> {
  const audience = process.env.WORKER_ID_TOKEN_AUDIENCE;
  if (!audience) return null;
  const now = Date.now();
  if (cachedIdToken && cachedIdToken.expiresAt > now) return cachedIdToken.token;

  const url =
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity" +
    `?audience=${encodeURIComponent(audience)}`;
  const res = await fetch(url, { headers: { "Metadata-Flavor": "Google" } });
  if (!res.ok) {
    throw new Error(`ID トークンを取得できませんでした（${res.status}）。`);
  }
  const token = (await res.text()).trim();
  // Google の ID トークンの有効期限は1時間。余裕をみて50分で取り直す。
  cachedIdToken = { token, expiresAt: now + 50 * 60 * 1000 };
  return token;
}

async function callWorker<T>(
  path: string,
  init: {
    method: "GET" | "POST" | "PATCH" | "DELETE";
    session?: Session | null;
    body?: unknown;
    formData?: FormData;
  }
): Promise<T> {
  const baseUrl = process.env.WORKER_BASE_URL;
  const sharedSecret = process.env.WORKER_SHARED_SECRET;
  if (!baseUrl || !sharedSecret) {
    throw new Error("WORKER_BASE_URL または WORKER_SHARED_SECRET が設定されていません。");
  }

  const headers: Record<string, string> = {
    "X-Worker-Secret": sharedSecret,
  };
  if (init.session) {
    headers["Authorization"] = `Bearer ${init.session.accessToken}`;
  }
  const idToken = await workerIdToken();
  if (idToken) {
    headers["X-Serverless-Authorization"] = `Bearer ${idToken}`;
  }

  let body: BodyInit | undefined;
  if (init.formData) {
    body = init.formData;
  } else if (init.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.body);
  }

  const res = await fetch(`${baseUrl}${path}`, { method: init.method, headers, body });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};

  if (!res.ok) {
    throw new WorkerApiError(res.status, data as WorkerError);
  }
  return data as T;
}

function requireSession(session: Session | null | undefined): Session {
  if (!session) {
    throw new WorkerApiError(401, { error: "unauthorized", message: "ログインが必要です。" });
  }
  return session;
}

// ---- §2.1 プロジェクト・メンバー ----

export async function w1ListMyProjects(session: Session | null): Promise<MyProjectSummary[]> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockListMyProjects(mockUserIdFromToken(s.accessToken));
  return callWorker<MyProjectSummary[]>("/internal/me/projects", { method: "GET", session: s });
}

export async function w2CreateProject(
  session: Session | null,
  input: CreateProjectInput
): Promise<CreateProjectOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    return mock.mockCreateProject(mockUserIdFromToken(s.accessToken), s.email, input);
  }
  return callWorker<CreateProjectOutput>("/internal/projects", { method: "POST", session: s, body: input });
}

export async function w3GetProject(session: Session | null, pid: string): Promise<ProjectDetail> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockGetProject(pid, mockUserIdFromToken(s.accessToken));
  return callWorker<ProjectDetail>(`/internal/projects/${pid}`, { method: "GET", session: s });
}

export async function w4UpdateProject(
  session: Session | null,
  pid: string,
  input: UpdateProjectInput
): Promise<UpdateProjectOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    return mock.mockUpdateProject(pid, mockUserIdFromToken(s.accessToken), input) as UpdateProjectOutput;
  }
  return callWorker<UpdateProjectOutput>(`/internal/projects/${pid}`, {
    method: "PATCH",
    session: s,
    body: input,
  });
}

export async function w5ListMembers(session: Session | null, pid: string): Promise<Member[]> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockListMembers(pid, mockUserIdFromToken(s.accessToken));
  return callWorker<Member[]>(`/internal/projects/${pid}/members`, { method: "GET", session: s });
}

export async function w6AddMember(
  session: Session | null,
  pid: string,
  input: AddMemberInput
): Promise<AddMemberOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    return mock.mockAddMember(pid, mockUserIdFromToken(s.accessToken), input.email, input.role);
  }
  return callWorker<AddMemberOutput>(`/internal/projects/${pid}/members`, {
    method: "POST",
    session: s,
    body: input,
  });
}

export async function w7UpdateMember(
  session: Session | null,
  pid: string,
  uid: string,
  input: UpdateMemberInput
): Promise<UpdateMemberOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    return mock.mockUpdateMember(pid, mockUserIdFromToken(s.accessToken), uid, input) as UpdateMemberOutput;
  }
  return callWorker<UpdateMemberOutput>(`/internal/projects/${pid}/members/${uid}`, {
    method: "PATCH",
    session: s,
    body: input,
  });
}

export async function w7bRemoveMember(
  session: Session | null,
  pid: string,
  uid: string
): Promise<RemoveMemberOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    return mock.mockRemoveMember(pid, mockUserIdFromToken(s.accessToken), uid);
  }
  return callWorker<RemoveMemberOutput>(`/internal/projects/${pid}/members/${uid}`, {
    method: "DELETE",
    session: s,
  });
}

// ---- §2.2 ソース ----

export async function w8CreateConversationSource(
  session: Session | null,
  pid: string,
  input: CreateConversationSourceInput
): Promise<CreateSourceOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    return mock.mockCreateConversationSource(pid, mockUserIdFromToken(s.accessToken), input);
  }
  return callWorker<CreateSourceOutput>(`/internal/projects/${pid}/sources/conversation`, {
    method: "POST",
    session: s,
    body: input,
  });
}

export async function w9CreateFileSource(
  session: Session | null,
  pid: string,
  file: File,
  recorded_at: string | undefined,
  visibility: Visibility
): Promise<CreateSourceOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    let content: string;
    try {
      content = await file.text();
    } catch {
      content = `(バイナリファイル: ${file.name})`;
    }
    return mock.mockCreateFileSource(
      pid,
      mockUserIdFromToken(s.accessToken),
      file.name,
      content,
      recorded_at,
      visibility,
      file.size
    );
  }
  const formData = new FormData();
  formData.set("file", file);
  if (recorded_at) formData.set("recorded_at", recorded_at);
  formData.set("visibility", visibility);
  return callWorker<CreateSourceOutput>(`/internal/projects/${pid}/sources/file`, {
    method: "POST",
    session: s,
    formData,
  });
}

export async function w10ListSources(session: Session | null, pid: string): Promise<SourceSummary[]> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockListSources(pid, mockUserIdFromToken(s.accessToken));
  return callWorker<SourceSummary[]>(`/internal/projects/${pid}/sources`, { method: "GET", session: s });
}

export async function w11UpdateVisibility(
  session: Session | null,
  sid: string,
  input: UpdateVisibilityInput
): Promise<UpdateSourceOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    const source = mock.mockUpdateVisibility(
      sid,
      mockUserIdFromToken(s.accessToken),
      input.visibility,
      input.reason
    );
    return { source };
  }
  return callWorker<UpdateSourceOutput>(`/internal/sources/${sid}/visibility`, {
    method: "PATCH",
    session: s,
    body: input,
  });
}

export async function w12UpdateExclusion(
  session: Session | null,
  sid: string,
  input: UpdateExclusionInput
): Promise<UpdateSourceOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    const source = mock.mockUpdateExclusion(
      sid,
      mockUserIdFromToken(s.accessToken),
      input.is_excluded,
      input.reason
    );
    return { source };
  }
  return callWorker<UpdateSourceOutput>(`/internal/sources/${sid}/exclusion`, {
    method: "PATCH",
    session: s,
    body: input,
  });
}

export async function w13GetDownloadUrl(session: Session | null, sid: string): Promise<DownloadUrlOutput> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockGetDownloadUrl(sid, mockUserIdFromToken(s.accessToken));
  return callWorker<DownloadUrlOutput>(`/internal/sources/${sid}/download-url`, {
    method: "GET",
    session: s,
  });
}

// ---- §2.3 実行 ----

export async function w14CreateRun(session: Session | null, pid: string): Promise<CreateRunOutput> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockCreateRun(pid, mockUserIdFromToken(s.accessToken));
  return callWorker<CreateRunOutput>(`/internal/projects/${pid}/runs`, { method: "POST", session: s });
}

export async function w15ExecuteRun(session: Session | null, rid: string): Promise<ExecuteRunOutput> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockExecuteRun(rid, mockUserIdFromToken(s.accessToken));
  return callWorker<ExecuteRunOutput>(`/internal/runs/${rid}/execute`, { method: "POST", session: s });
}

export async function w16GetRun(session: Session | null, rid: string): Promise<RunStatusOutput> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockGetRun(rid, mockUserIdFromToken(s.accessToken));
  return callWorker<RunStatusOutput>(`/internal/runs/${rid}`, { method: "GET", session: s });
}

// ---- §2.4 レポート ----

export async function w18ListReports(session: Session | null, pid: string): Promise<ReportSummary[]> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockListReports(pid, mockUserIdFromToken(s.accessToken));
  return callWorker<ReportSummary[]>(`/internal/projects/${pid}/reports`, { method: "GET", session: s });
}

export async function w19GetReport(
  session: Session | null,
  pid: string,
  versionNo: number
): Promise<ReportDetail> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockGetReport(pid, versionNo, mockUserIdFromToken(s.accessToken));
  // §2.4：版の種類は入力に取らない。クエリ文字列を一切付けない。
  return callWorker<ReportDetail>(`/internal/projects/${pid}/reports/${versionNo}`, {
    method: "GET",
    session: s,
  });
}

// ---- §2.5 通知・質問 ----

export async function w20ListNotifications(session: Session | null): Promise<NotificationItem[]> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockListNotifications(mockUserIdFromToken(s.accessToken));
  return callWorker<NotificationItem[]>("/internal/me/notifications", { method: "GET", session: s });
}

export async function w21MarkNotificationRead(
  session: Session | null,
  nid: string
): Promise<MarkNotificationReadOutput> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockMarkNotificationRead(nid, mockUserIdFromToken(s.accessToken));
  return callWorker<MarkNotificationReadOutput>(`/internal/me/notifications/${nid}/read`, {
    method: "POST",
    session: s,
  });
}

export async function w22GetQuestion(session: Session | null, qid: string): Promise<QuestionDetail> {
  const s = requireSession(session);
  if (isMockMode()) return mock.mockGetQuestion(qid, mockUserIdFromToken(s.accessToken));
  return callWorker<QuestionDetail>(`/internal/questions/${qid}`, { method: "GET", session: s });
}

export async function w23AnswerQuestion(
  session: Session | null,
  qid: string,
  input: AnswerQuestionInput
): Promise<AnswerQuestionOutput> {
  const s = requireSession(session);
  if (isMockMode()) {
    return mock.mockAnswerQuestion(qid, mockUserIdFromToken(s.accessToken), input.text);
  }
  return callWorker<AnswerQuestionOutput>(`/internal/questions/${qid}/answer`, {
    method: "POST",
    session: s,
    body: input,
  });
}
