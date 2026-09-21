/**
 * worker（Python / FastAPI）との契約となる型定義。
 * design/decision-trace/design.md §2（API一覧 W1〜W24）に1対1で対応する。
 * W17（Cloud Schedulerのみ）・W24（ヘルスチェック）は web からは呼ばないため型を持たない。
 *
 * 値の形はここが正。worker のレスポンスがこの形と食い違う場合はここを直すのではなく、
 * design.md に立ち戻って確認すること。
 */

// ---- 共通の値 ----

export type Role = "member" | "manager";
export type ProjectStatus = "active" | "completed";
export type Visibility = "all" | "managers_only";
export type SourceType = "file" | "conversation" | "answer";
export type ExcludeReason = "private" | "confidential" | "irrelevant" | "other";
export type RunTrigger = "manual" | "schedule";
export type RunStatus = "queued" | "running" | "done" | "failed";
export type RunStep =
  | "extracting"
  | "investigating"
  | "assembling"
  | "checking"
  | "notifying"
  | "done";
export type RunOutcome =
  | "report_created"
  | "no_new_events"
  | "locked"
  | "cost_limited"
  | "failed";
export type Audience = "all" | "managers";
export type JudgeStatus = "pass" | "flagged";
export type EventKind = "decision" | "rejected_option" | "open_issue" | "finding" | "status";
export type NotificationKind = "question" | "report" | "no_progress";
export type QuestionStatus = "open" | "answered" | "expired";

/** worker のエラー応答（{"error": "<コード>", "message": "<表示用の日本語>"}） */
export interface WorkerError {
  error: string;
  message: string;
}

export class WorkerApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, body: WorkerError) {
    super(body.message);
    this.status = status;
    this.code = body.error;
  }
}

// ---- §2.1 プロジェクト・メンバー ----

/** W1: GET /internal/me/projects */
export interface MyProjectSummary {
  project_id: string;
  name: string;
  role: Role;
  status: ProjectStatus;
  latest_version_no: number | null;
}

/** W2 入力 */
export interface CreateProjectInput {
  name: string;
  goal_description: string;
  readme_markdown?: string;
  no_progress_threshold_hours?: number;
  exclude_weekends?: boolean;
}

/** W2 出力 */
export interface CreateProjectOutput {
  project_id: string;
}

export interface Project {
  id: string;
  name: string;
  goal_description: string;
  readme_markdown: string | null;
  status: ProjectStatus;
  no_progress_threshold_hours: number;
  exclude_weekends: boolean;
  created_at: string;
}

export interface ExcludedSummary {
  count: number;
  by_reason: Record<ExcludeReason, number>;
}

export interface LatestRunSummary {
  run_id: string;
  status: RunStatus;
  step: RunStep | null;
  outcome: RunOutcome | null;
  version_no: number | null;
}

/** W3: GET /internal/projects/{pid} */
export interface ProjectDetail {
  project: Project;
  my_role: Role;
  latest_run?: LatestRunSummary | null;
  /** manager のときだけ値が入る。member には常に無い（null）。 */
  excluded_summary?: ExcludedSummary | null;
}

/** W4 入力 */
export interface UpdateProjectInput {
  name?: string;
  goal_description?: string;
  readme_markdown?: string;
  no_progress_threshold_hours?: number;
  exclude_weekends?: boolean;
  status?: ProjectStatus;
}

/** W4 出力 */
export interface UpdateProjectOutput {
  project: Project;
}

/** W5: GET /internal/projects/{pid}/members の要素 */
export interface Member {
  user_id: string;
  email: string;
  role: Role;
  notify_on_progress: boolean;
  notify_on_no_progress: boolean;
}

/** W6 入力 */
export interface AddMemberInput {
  email: string;
  role: Role;
}

/** W6 出力 */
export interface AddMemberOutput {
  user_id: string;
  role: Role;
}

/** W7 入力 */
export interface UpdateMemberInput {
  role?: Role;
  notify_on_progress?: boolean;
  notify_on_no_progress?: boolean;
}

/** W7 出力 */
export interface UpdateMemberOutput {
  member: Member;
}

/** W7b 出力 */
export interface RemoveMemberOutput {
  ok: true;
}

// ---- §2.2 ソース ----

/** W8 入力 */
export interface CreateConversationSourceInput {
  text: string;
  recorded_at?: string;
  visibility: Visibility;
}

/** W8・W9 共通の出力 */
export interface CreateSourceOutput {
  source_id: string;
  label_prefix: string;
  version_no: number;
  segment_count: number;
  redaction_count: number;
  new_segment_count?: number;
}

/** W9 入力（multipart のうち file 以外のフィールド） */
export interface CreateFileSourceFields {
  recorded_at?: string;
  visibility: Visibility;
}

/** W10: GET /internal/projects/{pid}/sources の要素 */
export interface SourceSummary {
  source_id: string;
  source_no: number;
  type: SourceType;
  filename: string | null;
  uploaded_by: string;
  recorded_at: string;
  visibility: Visibility;
  is_excluded: boolean;
  exclude_reason: ExcludeReason | null;
  current_version_no: number;
}

/** W11 入力 */
export interface UpdateVisibilityInput {
  visibility: Visibility;
  reason: ExcludeReason;
}

/** W12 入力 */
export interface UpdateExclusionInput {
  is_excluded: boolean;
  reason: ExcludeReason;
}

/** W11・W12 出力 */
export interface UpdateSourceOutput {
  source: SourceSummary;
}

/** W13 出力 */
export interface DownloadUrlOutput {
  url: string;
  expires_at: string;
}

// ---- §2.3 実行 ----

/** W14 出力 */
export interface CreateRunOutput {
  run_id: string;
  status: "queued";
}

/** W15 出力 */
export interface ExecuteRunOutput {
  run_id: string;
  status: RunStatus;
  version_no?: number | null;
  outcome: RunOutcome;
}

/** W16 出力 */
export interface RunStatusOutput {
  run_id: string;
  status: RunStatus;
  step: RunStep | null;
  outcome?: RunOutcome | null;
  version_no?: number | null;
  error_message?: string | null;
}

// ---- §2.4 レポート ----

/** W18: GET /internal/projects/{pid}/reports の要素 */
export interface ReportSummary {
  version_no: number;
  generated_at: string;
  audience: Audience;
  judge_status: JudgeStatus;
  withheld: boolean;
}

/** §5.6：脚注1件分の根拠 */
export interface FootnoteEntry {
  label: string;
  source_no: number;
  speaker: "user" | "ai" | "unknown" | null;
  recorded_at: string;
  text: string;
}

/** §5.6：図のノード1件分の根拠 */
export interface NodeEvidenceEntry {
  label: string;
  text: string;
}

export interface ReportFlags {
  suspected_injection: string[];
  unverified_ai_count: number;
}

export interface TimelineItem {
  event_no: number;
  occurred_at: string;
  kind: EventKind;
  summary: string;
}

/**
 * W19: GET /internal/projects/{pid}/reports/{version_no}
 * §5.6 のとおりの形（画面に出すものの全部）。
 */
export interface ReportDetail {
  version_no: number;
  audience: Audience;
  generated_at: string;
  judge_status: JudgeStatus;
  withheld: boolean;
  body_markdown: string;
  footnotes: Record<string, FootnoteEntry[]>;
  mermaid_dsl: string;
  node_evidence: Record<string, NodeEvidenceEntry[]>;
  flags: ReportFlags;
  timeline: TimelineItem[];
  excluded_summary: ExcludedSummary | null;
}

// ---- §2.5 通知・質問 ----

/** W20: GET /internal/me/notifications の要素 */
export interface NotificationItem {
  id: string;
  kind: NotificationKind;
  project_id: string;
  title: string;
  created_at: string;
  read_at: string | null;
  question_id: string | null;
}

/** W21 出力 */
export interface MarkNotificationReadOutput {
  ok: true;
}

/** W22 出力 */
export interface QuestionDetail {
  question_id: string;
  project_id: string;
  question: string;
  status: QuestionStatus;
  related_event_summary: string;
}

/** W23 入力 */
export interface AnswerQuestionInput {
  text: string;
}

/** W23 出力 */
export interface AnswerQuestionOutput {
  source_id: string;
}
