import "server-only";
import { WorkerApiError } from "@/lib/worker-types";
import type {
  AddMemberOutput,
  Audience,
  CreateSourceOutput,
  DownloadUrlOutput,
  EventSearchResult,
  ExcludeReason,
  ExcludedSummary,
  Member,
  NotificationItem,
  Project,
  ProjectDetail,
  QuestionDetail,
  ReportDetail,
  ReportSummary,
  Role,
  RunOutcome,
  RunStatusOutput,
  RunStep,
  SourceSummary,
  SpeakerCounts,
  Visibility,
} from "@/lib/worker-types";
import { MOCK_MANAGER, MOCK_MEMBER } from "@/lib/mock/users";

/**
 * WORKER_MOCK 用の固定データ + 最低限の可変ストア。
 * 本物の worker（Python）とは独立した、web だけで完結する仮のデータ源。
 * 実在の会社名・個人名は使わない。
 */

// 追加でメンバーに登録できる「登録済みだが未参加」の人物（W6のデモ用）。ログインには使わない。
export const MOCK_REGISTERABLE_USER = {
  id: "00000000-0000-4000-8000-000000000003",
  email: "extra-member@example.com",
};

function err(status: number, code: string, message: string): never {
  throw new WorkerApiError(status, { error: code, message });
}

// ---- 型（ストア内部だけで使う） ----

interface StoredSource {
  source_id: string;
  project_id: string;
  source_no: number;
  type: "file" | "conversation" | "answer";
  filename: string | null;
  uploaded_by: string;
  recorded_at: string;
  visibility: Visibility;
  is_excluded: boolean;
  exclude_reason: ExcludeReason | null;
  current_version_no: number;
  last_exclusion_change_by: string | null;
  /** source_audit_log の簡易版（design.md §2.2）。W11 の reason はここに記録するだけで、画面には出さない。 */
  visibility_audit: { changed_by: string; changed_at: string; reason: ExcludeReason }[];
  segments: StoredSegment[];
}

interface StoredSegment {
  label: string;
  seq: number;
  speaker: "user" | "ai" | "unknown";
  text: string;
}

interface StoredEvent {
  event_no: number;
  kind: "decision" | "rejected_option" | "open_issue" | "finding" | "status";
  summary: string;
  reason: string | null;
  occurred_at: string;
  segment_labels: string[];
  origin: "human_originated" | "ai_verified" | "ai_unverified" | "document" | null;
  partition: "all" | "managers";
}

interface StoredReport {
  project_id: string;
  version_no: number;
  audience: Audience;
  generated_at: string;
  judge_status: "pass" | "flagged";
  withheld: boolean;
  body_markdown: string;
  footnotes: ReportDetail["footnotes"];
  mermaid_dsl: ReportDetail["mermaid_dsl"];
  node_evidence: ReportDetail["node_evidence"];
  flags: ReportDetail["flags"];
  event_nos: number[];
}

interface StoredRun {
  run_id: string;
  project_id: string;
  status: "queued" | "running" | "done" | "failed";
  step: RunStep | null;
  outcome: RunOutcome | null;
  version_no: number | null;
  started_at: number | null;
}

interface StoredMember {
  user_id: string;
  email: string;
  role: Role;
  notify_on_progress: boolean;
  notify_on_no_progress: boolean;
}

interface StoredProject {
  project: Project;
  members: StoredMember[];
  sources: StoredSource[];
  events: StoredEvent[];
  reports: StoredReport[];
  nextSourceNo: number;
  nextEventNo: number;
  nextVersionNo: number;
  /** AD-9：日本時間の今日、daily_cost_alerts に行があるか（費用の上限に達して分析を行わなかったか）。 */
  costLimitedToday: boolean;
}

type StoredNotification = NotificationItem;

interface StoredQuestion extends QuestionDetail {
  asked_to: string;
}

// ---- 初期データ ----

const PROJECT_ID = "10000000-0000-4000-8000-000000000001";

const now = () => new Date().toISOString();

function seedProject(): StoredProject {
  const sources: StoredSource[] = [
    {
      source_id: "s-0000001",
      project_id: PROJECT_ID,
      source_no: 1,
      type: "conversation",
      filename: null,
      uploaded_by: MOCK_MANAGER.id,
      recorded_at: "2026-09-18T10:00:00+09:00",
      visibility: "all",
      is_excluded: false,
      exclude_reason: null,
      current_version_no: 1,
      last_exclusion_change_by: null,
      visibility_audit: [],
      segments: [
        {
          label: "S1-1",
          seq: 1,
          speaker: "user",
          text:
            'ユーザー: 「配信基盤は"Resend"にしよう」と決めた。理由：無料枠が大きい（1日100通まで）。#削除しない;優先度|高',
        },
        {
          label: "S1-2",
          seq: 2,
          speaker: "ai",
          text: "AI: 承知しました。ただし、次の設定は貼り付けないでください: <script>alert('secret')</script>",
        },
        {
          label: "S1-3",
          seq: 3,
          speaker: "user",
          text: "ユーザー: 通知メールの文面はまだ決めていない。次回までに考える。",
        },
        {
          label: "S1-4",
          seq: 4,
          speaker: "user",
          text: "ユーザー: 独自のメール配信システムを自作する案は、実装コストが見合わないので却下する。",
        },
      ],
    },
    {
      source_id: "s-0000002",
      project_id: PROJECT_ID,
      source_no: 2,
      type: "file",
      filename: "budget-note.md",
      uploaded_by: MOCK_MEMBER.id,
      recorded_at: "2026-09-19T09:30:00+09:00",
      visibility: "managers_only",
      is_excluded: false,
      exclude_reason: null,
      current_version_no: 1,
      last_exclusion_change_by: null,
      visibility_audit: [],
      segments: [
        {
          label: "S2-1",
          seq: 1,
          speaker: "unknown",
          text: "新規採用の給与レンジは非公開とする。担当者Aと担当者Bのみ閲覧可。",
        },
      ],
    },
    {
      source_id: "s-0000003",
      project_id: PROJECT_ID,
      source_no: 3,
      type: "conversation",
      filename: null,
      uploaded_by: MOCK_MEMBER.id,
      recorded_at: "2026-09-19T11:00:00+09:00",
      visibility: "all",
      is_excluded: true,
      exclude_reason: "irrelevant",
      current_version_no: 1,
      last_exclusion_change_by: MOCK_MEMBER.id,
      visibility_audit: [],
      segments: [
        { label: "S3-1", seq: 1, speaker: "user", text: "ユーザー: 今日のランチは何にしよう。" },
      ],
    },
    {
      source_id: "s-0000004",
      project_id: PROJECT_ID,
      source_no: 4,
      type: "file",
      filename: "old-draft.txt",
      uploaded_by: MOCK_MANAGER.id,
      recorded_at: "2026-09-17T08:00:00+09:00",
      visibility: "all",
      is_excluded: true,
      exclude_reason: "private",
      current_version_no: 1,
      last_exclusion_change_by: MOCK_MANAGER.id,
      visibility_audit: [],
      segments: [{ label: "S4-1", seq: 1, speaker: "unknown", text: "下書き（非公開）。" }],
    },
  ];

  const events: StoredEvent[] = [
    {
      event_no: 12,
      kind: "decision",
      summary: '配信基盤は"Resend"（1日100通まで）を採用 <確認済>',
      reason: "無料枠が大きいため（1日100通まで）",
      occurred_at: "2026-09-18T10:00:00+09:00",
      segment_labels: ["S1-1"],
      origin: "ai_verified",
      partition: "all",
    },
    {
      event_no: 13,
      kind: "open_issue",
      summary: "通知メールの文面は未定",
      reason: null,
      occurred_at: "2026-09-18T10:05:00+09:00",
      segment_labels: ["S1-3"],
      origin: null,
      partition: "all",
    },
    {
      event_no: 14,
      kind: "finding",
      summary: "AIの応答に不審な指示混入の疑いがあります",
      reason: null,
      occurred_at: "2026-09-18T10:02:00+09:00",
      segment_labels: ["S1-2"],
      origin: null,
      partition: "all",
    },
    {
      event_no: 15,
      kind: "rejected_option",
      summary: "独自メール配信システムの自作は却下",
      reason: "実装コストが見合わないため",
      occurred_at: "2026-09-18T10:06:00+09:00",
      segment_labels: ["S1-4"],
      origin: "human_originated",
      partition: "all",
    },
    {
      event_no: 16,
      kind: "decision",
      summary: "通知文面のトーンはカジュアルにする",
      reason: "AIの提案のまま、人による確認はまだ",
      occurred_at: "2026-09-18T10:07:00+09:00",
      segment_labels: ["S1-3"],
      origin: "ai_unverified",
      partition: "all",
    },
    {
      event_no: 20,
      kind: "decision",
      summary: "新規採用の給与レンジは非公開のまま管理する",
      reason: "個人情報を含むため（担当者A・担当者Bのみ閲覧）",
      occurred_at: "2026-09-19T09:30:00+09:00",
      segment_labels: ["S2-1"],
      origin: "human_originated",
      partition: "managers",
    },
    {
      event_no: 30,
      kind: "decision",
      summary: "図の描画に失敗した場合のフォールバック確認用データ",
      reason: "mermaid_dsl をわざと壊して、一覧表示への切り替えを確認するため",
      occurred_at: "2026-09-20T15:00:00+09:00",
      segment_labels: ["S1-1"],
      origin: "human_originated",
      partition: "all",
    },
    {
      // AD-10：却下案の再浮上の検知の確認用（event_no 15「独自メール配信システムの自作は却下」と類似）。
      event_no: 31,
      kind: "decision",
      summary: "独自の配信システムを自作する方針に転換",
      reason: "外部サービスの障害が続いたため",
      occurred_at: "2026-09-21T09:00:00+09:00",
      segment_labels: ["S1-1"],
      origin: "human_originated",
      partition: "all",
    },
  ];

  const allEventNos = [12, 13, 14, 15, 16, 31];
  const managersEventNos = [12, 13, 14, 15, 16, 20, 31];

  const bodyAll = [
    "## 今回決定したこと（未確認：1件）",
    '- 配信基盤は"Resend"（1日100通まで）を採用 〔AIの提案を人が確認〕[^12]',
    "- 通知文面のトーンはカジュアルにする 〔AIの提案のまま（未確認）〕[^16]",
    "- 独自の配信システムを自作する方針に転換 〔過去に却下した案と類似：E15〕[^31]",
    "",
    "## 却下した案",
    "- 独自メール配信システムの自作は却下[^15]",
    "",
    "## 未解決の論点",
    "- 通知メールの文面は未定[^13]",
    "",
    "## 気になる点",
    "- AIの応答に不審な指示混入の疑いがあります[^14]",
    "",
  ].join("\n");

  const bodyManagers = [
    bodyAll,
    "## 管理者限定の決定",
    "- 新規採用の給与レンジは非公開のまま管理する 〔人が発案〕[^20]",
    "",
  ].join("\n");

  const footnotesAll: ReportDetail["footnotes"] = {
    "12": [
      {
        label: "S1-1",
        source_no: 1,
        speaker: "user",
        recorded_at: "2026-09-18T10:00:00+09:00",
        text: sources[0].segments[0].text,
      },
    ],
    "13": [
      {
        label: "S1-3",
        source_no: 1,
        speaker: "user",
        recorded_at: "2026-09-18T10:00:00+09:00",
        text: sources[0].segments[2].text,
      },
    ],
    "14": [
      {
        label: "S1-2",
        source_no: 1,
        speaker: "ai",
        recorded_at: "2026-09-18T10:00:00+09:00",
        text: sources[0].segments[1].text,
      },
    ],
    "15": [
      {
        label: "S1-4",
        source_no: 1,
        speaker: "user",
        recorded_at: "2026-09-18T10:00:00+09:00",
        text: sources[0].segments[3].text,
      },
    ],
    "16": [
      {
        label: "S1-3",
        source_no: 1,
        speaker: "user",
        recorded_at: "2026-09-18T10:00:00+09:00",
        text: sources[0].segments[2].text,
      },
    ],
    "31": [
      {
        label: "S1-1",
        source_no: 1,
        speaker: "user",
        recorded_at: "2026-09-18T10:00:00+09:00",
        text: sources[0].segments[0].text,
      },
    ],
  };

  const footnotesManagers: ReportDetail["footnotes"] = {
    ...footnotesAll,
    "20": [
      {
        label: "S2-1",
        source_no: 2,
        speaker: "unknown",
        recorded_at: "2026-09-19T09:30:00+09:00",
        text: sources[1].segments[0].text,
      },
    ],
  };

  const nodeEvidenceAll: ReportDetail["node_evidence"] = {
    E12: [{ label: "S1-1", text: sources[0].segments[0].text }],
    E13: [{ label: "S1-3", text: sources[0].segments[2].text }],
    E14: [{ label: "S1-2", text: sources[0].segments[1].text }],
    E15: [{ label: "S1-4", text: sources[0].segments[3].text }],
    E16: [{ label: "S1-3", text: sources[0].segments[2].text }],
    E31: [{ label: "S1-1", text: sources[0].segments[0].text }],
  };

  const nodeEvidenceManagers: ReportDetail["node_evidence"] = {
    ...nodeEvidenceAll,
    E20: [{ label: "S2-1", text: sources[1].segments[0].text }],
  };

  const mermaidAll = [
    "flowchart TD",
    '  E12["配信基盤は＂Resend＂（1日100通まで）を採用 ＜確認済＞"]',
    '  E16["通知文面のトーンはカジュアルにする"]',
    '  E15{{"独自メール配信システムの自作は却下"}}',
    '  E13(("通知メールの文面は未定"))',
    '  E14[/"AIの応答に不審な指示混入の疑いがあります"/]',
    "  E12 --> E16",
  ].join("\n");

  const mermaidManagers = [
    mermaidAll,
    '  E20["新規採用の給与レンジは非公開のまま管理する"]',
  ].join("\n");

  const reports: StoredReport[] = [
    {
      project_id: PROJECT_ID,
      version_no: 1,
      audience: "all",
      generated_at: "2026-09-18T10:10:00+09:00",
      judge_status: "pass",
      withheld: false,
      body_markdown: bodyAll,
      footnotes: footnotesAll,
      mermaid_dsl: mermaidAll,
      node_evidence: nodeEvidenceAll,
      flags: {
        suspected_injection: ["S1-2"],
        unverified_ai_count: 1,
        rejected_similarity: [{ event_no: 31, rejected_event_no: 15 }],
      },
      event_nos: allEventNos,
    },
    {
      project_id: PROJECT_ID,
      version_no: 1,
      audience: "managers",
      generated_at: "2026-09-18T10:10:00+09:00",
      judge_status: "pass",
      withheld: false,
      body_markdown: bodyManagers,
      footnotes: footnotesManagers,
      mermaid_dsl: mermaidManagers,
      node_evidence: nodeEvidenceManagers,
      flags: {
        suspected_injection: ["S1-2"],
        unverified_ai_count: 1,
        rejected_similarity: [{ event_no: 31, rejected_event_no: 15 }],
      },
      event_nos: managersEventNos,
    },
    {
      // §5.5：後から可視性が変わり、閲覧不可になった版の表示確認用（withheld=true）。
      // 形は worker の実際の応答（api/reports.py の withheld 分岐）に合わせてある：
      // body_markdown は表示用メッセージ、mermaid_dsl は null、flags は空オブジェクト。
      project_id: PROJECT_ID,
      version_no: 2,
      audience: "all",
      generated_at: "2026-09-19T12:00:00+09:00",
      judge_status: "pass",
      withheld: true,
      body_markdown:
        "この版には非公開になった情報が含まれるため表示できません。次の実行で作り直されます。",
      footnotes: {},
      mermaid_dsl: null,
      node_evidence: {},
      flags: {},
      event_nos: [],
    },
    {
      // §7 追加1：mermaid の描画に失敗した場合のフォールバック確認用（mermaid_dsl が壊れている）。
      project_id: PROJECT_ID,
      version_no: 3,
      audience: "all",
      generated_at: "2026-09-20T15:05:00+09:00",
      judge_status: "pass",
      withheld: false,
      body_markdown: [
        "## 今回決定したこと（未確認：0件）",
        "- 図の描画に失敗した場合のフォールバック確認用データ[^30]",
        "",
      ].join("\n"),
      footnotes: {
        "30": [
          {
            label: "S1-1",
            source_no: 1,
            speaker: "user",
            recorded_at: "2026-09-18T10:00:00+09:00",
            text: sources[0].segments[0].text,
          },
        ],
      },
      // 意図的に壊れた mermaid（閉じ括弧が無い・矢印の行き先が無い）。
      mermaid_dsl: 'flowchart TD\n  E30["図の描画に失敗した場合のフォールバック確認用データ\n  E30 -->\n',
      node_evidence: { E30: [{ label: "S1-1", text: sources[0].segments[0].text }] },
      flags: { suspected_injection: [], unverified_ai_count: 0 },
      event_nos: [30],
    },
  ];

  return {
    project: {
      id: PROJECT_ID,
      name: "決定ログ実証プロジェクト",
      goal_description: "チームの意思決定と根拠を自動で追跡し、レポートにまとめる仕組みを検証する。",
      readme_markdown: "## 概要\nこれはモックモードのデモ用プロジェクトです。",
      status: "active",
      no_progress_threshold_hours: 24,
      exclude_weekends: true,
      created_at: "2026-09-17T08:00:00+09:00",
    },
    members: [
      {
        user_id: MOCK_MANAGER.id,
        email: MOCK_MANAGER.email,
        role: "manager",
        notify_on_progress: true,
        notify_on_no_progress: true,
      },
      {
        user_id: MOCK_MEMBER.id,
        email: MOCK_MEMBER.email,
        role: "member",
        notify_on_progress: true,
        notify_on_no_progress: false,
      },
    ],
    sources,
    events,
    reports,
    nextSourceNo: 5,
    nextEventNo: 32,
    nextVersionNo: 4,
    // AD-9：完了条件（帯の表示確認）をそのまま curl で確かめられるよう、既定で true にしておく。
    costLimitedToday: true,
  };
}

// モジュールスコープの可変ストア（開発サーバーのプロセス内でのみ保持される）。
//
// Next.js は Server Component（RSC）用と Route Handler 用でこのモジュールを別々に
// バンドルすることがあり、素の module スコープ変数だとレイヤーごとに別インスタンスが
// でき、Route Handler で書いた変更が Server Component から見えない事故が起きる。
// そのため globalThis に載せ、プロセス内で必ず1つだけ作られるようにする。
interface MockState {
  projects: Map<string, StoredProject>;
  runs: Map<string, StoredRun>;
  runSeq: number;
  notifications: StoredNotification[];
  questions: Map<string, StoredQuestion>;
}

declare global {
  var __decisionTraceMockState: MockState | undefined;
}

function createInitialState(): MockState {
  return {
    projects: new Map<string, StoredProject>([[PROJECT_ID, seedProject()]]),
    runs: new Map<string, StoredRun>(),
    runSeq: 1,
    notifications: [
      {
        id: "n-0000001",
        kind: "question",
        project_id: PROJECT_ID,
        title: "「通知メールの文面」について確認したいことがあります",
        created_at: "2026-09-18T10:30:00+09:00",
        read_at: null,
        question_id: "q-0000001",
      },
      {
        id: "n-0000002",
        kind: "report",
        project_id: PROJECT_ID,
        title: "新しいレポート（版1）ができました",
        created_at: "2026-09-18T10:10:00+09:00",
        read_at: "2026-09-18T11:00:00+09:00",
        question_id: null,
      },
      {
        // AD-9：manager にだけ1日1回届く通知（見え方の確認用）。
        id: "n-0000003",
        kind: "cost_limited",
        project_id: PROJECT_ID,
        title: "本日は費用の上限に達したため、分析を行っていません",
        created_at: "2026-09-22T09:00:00+09:00",
        read_at: null,
        question_id: null,
      },
    ],
    questions: new Map<string, StoredQuestion>([
      [
        "q-0000001",
        {
          question_id: "q-0000001",
          project_id: PROJECT_ID,
          question:
            '「通知メールの文面はまだ決めていない」とありますが、既定のトーン（フォーマル／カジュアル）はどちらにしますか？',
          status: "open",
          related_event_summary: "通知メールの文面は未定",
          asked_to: MOCK_MEMBER.id,
        },
      ],
    ]),
  };
}

const state = globalThis.__decisionTraceMockState ?? createInitialState();
globalThis.__decisionTraceMockState = state;

const { projects, runs, notifications, questions } = state;

// ---- ヘルパー ----

function getProjectOr404(pid: string): StoredProject {
  const p = projects.get(pid);
  if (!p) err(404, "not_found", "プロジェクトが見つかりません。");
  return p;
}

function getMemberOr404(p: StoredProject, userId: string): StoredMember {
  const m = p.members.find((x) => x.user_id === userId);
  if (!m) err(404, "not_found", "プロジェクトが見つかりません。");
  return m;
}

function findProjectBySourceId(sid: string): { project: StoredProject; source: StoredSource } {
  for (const p of projects.values()) {
    const s = p.sources.find((x) => x.source_id === sid);
    if (s) return { project: p, source: s };
  }
  err(404, "not_found", "ソースが見つかりません。");
}

function findProjectByRunId(rid: string): { project: StoredProject; run: StoredRun } {
  const run = runs.get(rid);
  if (!run) err(404, "not_found", "実行が見つかりません。");
  const project = getProjectOr404(run.project_id);
  return { project, run };
}

function canView(source: StoredSource, userId: string, role: Role): boolean {
  if (role === "manager") return true;
  return source.visibility === "all" || source.uploaded_by === userId;
}

function toSourceSummary(s: StoredSource): SourceSummary {
  return {
    source_id: s.source_id,
    source_no: s.source_no,
    type: s.type,
    filename: s.filename,
    uploaded_by: s.uploaded_by,
    recorded_at: s.recorded_at,
    visibility: s.visibility,
    is_excluded: s.is_excluded,
    exclude_reason: s.exclude_reason,
    current_version_no: s.current_version_no,
  };
}

function computeExcludedSummary(p: StoredProject): ExcludedSummary {
  const by_reason: Record<ExcludeReason, number> = {
    private: 0,
    confidential: 0,
    irrelevant: 0,
    other: 0,
  };
  let count = 0;
  for (const s of p.sources) {
    if (s.is_excluded && s.exclude_reason) {
      count += 1;
      by_reason[s.exclude_reason] += 1;
    }
  }
  return { count, by_reason };
}

/** その版を、この役割の閲覧者に見せてよいか（audience の解決）。W18・W19 共通。 */
function pickReportForRole(p: StoredProject, versionNo: number, role: Role): StoredReport | null {
  const rows = p.reports.filter((r) => r.version_no === versionNo);
  if (role === "manager") {
    return rows.find((r) => r.audience === "managers") ?? rows.find((r) => r.audience === "all") ?? null;
  }
  return rows.find((r) => r.audience === "all") ?? null;
}

function toReportSummary(r: StoredReport): ReportSummary {
  return {
    version_no: r.version_no,
    generated_at: r.generated_at,
    audience: r.audience,
    judge_status: r.judge_status,
    withheld: r.withheld,
  };
}

// ---- W1〜W7b：プロジェクト・メンバー ----

export function mockListMyProjects(userId: string) {
  const out: {
    project_id: string;
    name: string;
    role: Role;
    status: Project["status"];
    latest_version_no: number | null;
  }[] = [];
  for (const p of projects.values()) {
    const m = p.members.find((x) => x.user_id === userId);
    if (!m) continue;
    const versions = p.reports.map((r) => r.version_no);
    out.push({
      project_id: p.project.id,
      name: p.project.name,
      role: m.role,
      status: p.project.status,
      latest_version_no: versions.length ? Math.max(...versions) : null,
    });
  }
  return out;
}

export function mockCreateProject(
  userId: string,
  userEmail: string,
  input: { name: string; goal_description: string; readme_markdown?: string; no_progress_threshold_hours?: number; exclude_weekends?: boolean }
) {
  if (!input.name?.trim() || !input.goal_description?.trim()) {
    err(400, "invalid_input", "プロジェクト名と目的の説明は必須です。");
  }
  const id = `p-${Math.random().toString(36).slice(2, 10)}`;
  const stored: StoredProject = {
    project: {
      id,
      name: input.name,
      goal_description: input.goal_description,
      readme_markdown: input.readme_markdown ?? null,
      status: "active",
      no_progress_threshold_hours: input.no_progress_threshold_hours ?? 24,
      exclude_weekends: input.exclude_weekends ?? false,
      created_at: now(),
    },
    members: [
      {
        user_id: userId,
        email: userEmail,
        role: "manager",
        notify_on_progress: true,
        notify_on_no_progress: true,
      },
    ],
    sources: [],
    events: [],
    reports: [],
    nextSourceNo: 1,
    nextEventNo: 1,
    nextVersionNo: 1,
    costLimitedToday: false,
  };
  projects.set(id, stored);
  return { project_id: id };
}

export function mockGetProject(pid: string, userId: string): ProjectDetail {
  const p = getProjectOr404(pid);
  const member = getMemberOr404(p, userId);
  return {
    project: p.project,
    my_role: member.role,
    excluded_summary: member.role === "manager" ? computeExcludedSummary(p) : null,
    cost_limited_today: p.costLimitedToday,
  };
}

export function mockUpdateProject(
  pid: string,
  userId: string,
  input: Partial<Project> & { status?: Project["status"] }
) {
  const p = getProjectOr404(pid);
  const member = getMemberOr404(p, userId);
  if (member.role !== "manager") err(403, "forbidden", "manager だけが変更できます。");
  Object.assign(p.project, input);
  return { project: p.project };
}

export function mockListMembers(pid: string, userId: string): Member[] {
  const p = getProjectOr404(pid);
  getMemberOr404(p, userId);
  return p.members.map((m) => ({ ...m }));
}

export function mockAddMember(pid: string, userId: string, email: string, role: Role): AddMemberOutput {
  const p = getProjectOr404(pid);
  const requester = getMemberOr404(p, userId);
  if (requester.role !== "manager") err(403, "forbidden", "manager だけが追加できます。");
  const candidates = [MOCK_MANAGER, MOCK_MEMBER, MOCK_REGISTERABLE_USER];
  const found = candidates.find((c) => c.email.toLowerCase() === email.trim().toLowerCase());
  if (!found) err(404, "user_not_found", "先にサインアップしてもらってください。");
  if (p.members.some((m) => m.user_id === found.id)) {
    err(409, "already_member", "すでにメンバーです。");
  }
  p.members.push({
    user_id: found.id,
    email: found.email,
    role,
    notify_on_progress: true,
    notify_on_no_progress: true,
  });
  return { user_id: found.id, role };
}

export function mockUpdateMember(
  pid: string,
  userId: string,
  targetUserId: string,
  input: { role?: Role; notify_on_progress?: boolean; notify_on_no_progress?: boolean }
) {
  const p = getProjectOr404(pid);
  const requester = getMemberOr404(p, userId);
  const target = p.members.find((m) => m.user_id === targetUserId);
  if (!target) err(404, "not_found", "メンバーが見つかりません。");
  if (input.role !== undefined) {
    if (requester.role !== "manager") err(403, "forbidden", "manager だけがロールを変更できます。");
    if (target.role === "manager" && input.role === "member") {
      const managerCount = p.members.filter((m) => m.role === "manager").length;
      if (managerCount <= 1) err(409, "last_manager", "最後の manager を降格することはできません。");
    }
    target.role = input.role;
  }
  if (input.notify_on_progress !== undefined || input.notify_on_no_progress !== undefined) {
    if (requester.role !== "manager" && requester.user_id !== targetUserId) {
      err(403, "forbidden", "本人か manager だけが通知設定を変更できます。");
    }
    if (input.notify_on_progress !== undefined) target.notify_on_progress = input.notify_on_progress;
    if (input.notify_on_no_progress !== undefined) target.notify_on_no_progress = input.notify_on_no_progress;
  }
  return { member: { ...target } };
}

export function mockRemoveMember(pid: string, userId: string, targetUserId: string) {
  const p = getProjectOr404(pid);
  const requester = getMemberOr404(p, userId);
  if (requester.role !== "manager") err(403, "forbidden", "manager だけが削除できます。");
  const target = p.members.find((m) => m.user_id === targetUserId);
  if (!target) err(404, "not_found", "メンバーが見つかりません。");
  if (target.role === "manager") {
    const managerCount = p.members.filter((m) => m.role === "manager").length;
    if (managerCount <= 1) err(409, "last_manager", "最後の manager は削除できません。");
  }
  p.members = p.members.filter((m) => m.user_id !== targetUserId);
  return { ok: true as const };
}

// ---- W8〜W13：ソース ----

// design.md §4 の1／worker の _ALLOWED_EXTENSIONS・_MAX_FILE_BYTES と同じ制限。
const ALLOWED_FILE_EXTENSIONS = new Set([
  ".txt",
  ".md",
  ".csv",
  ".py",
  ".ts",
  ".js",
  ".json",
  ".xlsx",
  ".docx",
  ".pptx",
  ".pdf",
]);
const MAX_FILE_BYTES = 10 * 1024 * 1024;

function redactCount(text: string): number {
  // モック用の簡易な伏せ字カウント（本物の redact.py は worker 側にある）。
  const patterns = [/sk-[A-Za-z0-9]{10,}/g, /AKIA[0-9A-Z]{16}/g, /-----BEGIN [A-Z ]*PRIVATE KEY-----/g];
  return patterns.reduce((sum, re) => sum + (text.match(re)?.length ?? 0), 0);
}

function segmentize(text: string): string[] {
  return text
    .split(/\n{2,}/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 200);
}

// AD-8：区切りの先頭が話者の目印（「ユーザー:」「AI:」等）で始まるかを見る、モック用の簡易判定。
// 本物の話者判定は worker 側にある。
function detectSpeaker(text: string): "user" | "ai" | "unknown" {
  if (/^\s*(user|ユーザー)\s*[:：]/i.test(text)) return "user";
  if (/^\s*(ai|assistant|アシスタント)\s*[:：]/i.test(text)) return "ai";
  return "unknown";
}

export function mockCreateConversationSource(
  pid: string,
  userId: string,
  input: { text: string; recorded_at?: string; visibility: Visibility }
): CreateSourceOutput {
  const p = getProjectOr404(pid);
  getMemberOr404(p, userId);
  if (!input.text?.trim()) err(400, "invalid_input", "本文が空です。");
  if (input.text.length > 200000) err(400, "too_large", "貼り付けは200,000文字までです。");
  const sourceNo = p.nextSourceNo++;
  const chunks = segmentize(input.text);
  const segments: StoredSegment[] = chunks.map((t, i) => ({
    label: `S${sourceNo}-${i + 1}`,
    seq: i + 1,
    speaker: detectSpeaker(t),
    text: t,
  }));
  const source: StoredSource = {
    source_id: `s-${Math.random().toString(36).slice(2, 10)}`,
    project_id: pid,
    source_no: sourceNo,
    type: "conversation",
    filename: null,
    uploaded_by: userId,
    recorded_at: input.recorded_at ?? now(),
    visibility: input.visibility,
    is_excluded: false,
    exclude_reason: null,
    current_version_no: 1,
    last_exclusion_change_by: null,
    visibility_audit: [],
    segments,
  };
  p.sources.push(source);
  const speaker_counts: SpeakerCounts = { user: 0, ai: 0, unknown: 0 };
  for (const seg of segments) speaker_counts[seg.speaker] += 1;
  return {
    source_id: source.source_id,
    label_prefix: `S${sourceNo}`,
    version_no: 1,
    segment_count: segments.length,
    redaction_count: redactCount(input.text),
    speaker_counts,
  };
}

export function mockCreateFileSource(
  pid: string,
  userId: string,
  filename: string,
  content: string,
  recorded_at: string | undefined,
  visibility: Visibility,
  fileSize: number
): CreateSourceOutput {
  const p = getProjectOr404(pid);
  getMemberOr404(p, userId);
  const dotIndex = filename.lastIndexOf(".");
  const suffix = dotIndex >= 0 ? filename.slice(dotIndex).toLowerCase() : "";
  if (!ALLOWED_FILE_EXTENSIONS.has(suffix)) {
    err(400, "unsupported_file_type", `${suffix || "(拡張子なし)"} は取り込めません。`);
  }
  if (fileSize > MAX_FILE_BYTES) {
    err(400, "file_too_large", "ファイルは10MBまでです。");
  }
  const existing = p.sources.find(
    (s) => s.type === "file" && s.uploaded_by === userId && s.filename === filename
  );
  const chunks = segmentize(content);
  if (existing) {
    const startSeq = existing.segments.length;
    const newSegments: StoredSegment[] = chunks.map((t, i) => ({
      label: `S${existing.source_no}-${startSeq + i + 1}`,
      seq: startSeq + i + 1,
      speaker: "unknown",
      text: t,
    }));
    existing.segments.push(...newSegments);
    existing.current_version_no += 1;
    // 再アップロードでは可視性を変えない（既存の値のまま）。
    return {
      source_id: existing.source_id,
      label_prefix: `S${existing.source_no}`,
      version_no: existing.current_version_no,
      segment_count: existing.segments.length,
      redaction_count: redactCount(content),
      new_segment_count: newSegments.length,
    };
  }
  const sourceNo = p.nextSourceNo++;
  const segments: StoredSegment[] = chunks.map((t, i) => ({
    label: `S${sourceNo}-${i + 1}`,
    seq: i + 1,
    speaker: "unknown",
    text: t,
  }));
  const source: StoredSource = {
    source_id: `s-${Math.random().toString(36).slice(2, 10)}`,
    project_id: pid,
    source_no: sourceNo,
    type: "file",
    filename,
    uploaded_by: userId,
    recorded_at: recorded_at ?? now(),
    visibility,
    is_excluded: false,
    exclude_reason: null,
    current_version_no: 1,
    last_exclusion_change_by: null,
    visibility_audit: [],
    segments,
  };
  p.sources.push(source);
  return {
    source_id: source.source_id,
    label_prefix: `S${sourceNo}`,
    version_no: 1,
    segment_count: segments.length,
    redaction_count: redactCount(content),
  };
}

export function mockListSources(pid: string, userId: string): SourceSummary[] {
  const p = getProjectOr404(pid);
  const member = getMemberOr404(p, userId);
  return p.sources.filter((s) => canView(s, userId, member.role)).map(toSourceSummary);
}

export function mockUpdateVisibility(
  sid: string,
  userId: string,
  visibility: Visibility,
  reason: ExcludeReason
): SourceSummary {
  const { project: p, source: s } = findProjectBySourceId(sid);
  const member = getMemberOr404(p, userId);
  if (visibility === "managers_only") {
    if (member.role !== "manager" && s.uploaded_by !== userId) {
      err(403, "forbidden", "自分が登録したソースのみ変更できます。");
    }
  } else {
    if (member.role !== "manager") err(403, "forbidden", "manager だけが公開に戻せます。");
  }
  s.visibility = visibility;
  s.visibility_audit.push({ changed_by: userId, changed_at: now(), reason });
  return toSourceSummary(s);
}

export function mockUpdateExclusion(
  sid: string,
  userId: string,
  is_excluded: boolean,
  reason: ExcludeReason
): SourceSummary {
  const { project: p, source: s } = findProjectBySourceId(sid);
  const member = getMemberOr404(p, userId);
  if (is_excluded) {
    if (member.role !== "manager" && s.uploaded_by !== userId) {
      err(403, "forbidden", "自分が登録したソースのみ除外できます。");
    }
  } else {
    if (member.role !== "manager") {
      if (s.uploaded_by !== userId || s.last_exclusion_change_by !== userId) {
        err(403, "forbidden", "自分が除外した、自分のソースのみ戻せます。");
      }
    }
  }
  s.is_excluded = is_excluded;
  s.exclude_reason = is_excluded ? reason : null;
  s.last_exclusion_change_by = userId;
  return toSourceSummary(s);
}

export function mockGetDownloadUrl(sid: string, userId: string): DownloadUrlOutput {
  const { project: p, source: s } = findProjectBySourceId(sid);
  const member = getMemberOr404(p, userId);
  if (!canView(s, userId, member.role)) err(404, "not_found", "ソースが見つかりません。");
  return {
    url: `https://mock-storage.example.com/${p.project.id}/${s.source_id}?expires=60`,
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  };
}

// ---- W14〜W16：実行 ----

const RUN_STEPS: RunStep[] = ["extracting", "investigating", "assembling", "checking", "notifying", "done"];
const STEP_DURATION_MS = 1200;

export function mockCreateRun(pid: string, userId: string) {
  const p = getProjectOr404(pid);
  getMemberOr404(p, userId);
  const run_id = `r-${state.runSeq++}`;
  runs.set(run_id, {
    run_id,
    project_id: pid,
    status: "queued",
    step: null,
    outcome: null,
    version_no: null,
    started_at: null,
  });
  return { run_id, status: "queued" as const };
}

export function mockExecuteRun(rid: string, userId: string) {
  const { project: p, run } = findProjectByRunId(rid);
  getMemberOr404(p, userId);
  if (run.status !== "queued") {
    // worker（run.py の execute_run）はここをエラーにしない。二重送信で
    // 引き受けられなかった側は、今の状態をそのまま返す（未確定なら outcome は
    // "locked" 扱い）。
    return {
      run_id: rid,
      status: run.status,
      outcome: run.outcome ?? ("locked" as const),
      version_no: run.version_no,
    };
  }
  run.status = "running";
  run.started_at = Date.now();
  run.step = RUN_STEPS[0];
  // フロントは応答を待たない前提（§5.1）。ここではバックグラウンドの進行はポーリング側で計算する。
  const versionNo = p.nextVersionNo++;
  setTimeout(
    () => {
      run.status = "done";
      run.step = "done";
      run.outcome = "report_created";
      run.version_no = versionNo;

      // モックには本物の抽出パイプラインが無いため、デモ用に「全員向けの最新の版」を
      // 新しい版番号で複製して積む（§5.1 の一連の流れを最後まで確認できるようにするため）。
      const latestAll = [...p.reports].reverse().find((r) => r.audience === "all" && !r.withheld);
      if (latestAll) {
        p.reports.push({ ...latestAll, version_no: versionNo, generated_at: now(), withheld: false });
      }
    },
    STEP_DURATION_MS * RUN_STEPS.length
  );
  return { run_id: rid, status: "running" as const, outcome: "report_created" as const, version_no: versionNo };
}

export function mockGetRun(rid: string, userId: string): RunStatusOutput {
  const { project: p, run } = findProjectByRunId(rid);
  getMemberOr404(p, userId);
  if (run.status === "running" && run.started_at) {
    const elapsed = Date.now() - run.started_at;
    const idx = Math.min(Math.floor(elapsed / STEP_DURATION_MS), RUN_STEPS.length - 1);
    run.step = RUN_STEPS[idx];
  }
  return {
    run_id: run.run_id,
    status: run.status,
    step: run.step,
    outcome: run.outcome,
    version_no: run.version_no,
  };
}

// ---- W18〜W19：レポート ----

export function mockListReports(pid: string, userId: string): ReportSummary[] {
  const p = getProjectOr404(pid);
  const member = getMemberOr404(p, userId);
  const versionNos = Array.from(new Set(p.reports.map((r) => r.version_no))).sort((a, b) => a - b);
  const out: ReportSummary[] = [];
  for (const v of versionNos) {
    const r = pickReportForRole(p, v, member.role);
    if (r) out.push(toReportSummary(r));
  }
  return out;
}

export function mockGetReport(pid: string, versionNo: number, userId: string): ReportDetail {
  const p = getProjectOr404(pid);
  const member = getMemberOr404(p, userId);
  const r = pickReportForRole(p, versionNo, member.role);
  if (!r) err(404, "not_found", "レポートが見つかりません。");
  const timeline = r.event_nos
    .map((no) => p.events.find((e) => e.event_no === no))
    .filter((e): e is StoredEvent => Boolean(e))
    .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
    .map((e) => ({ event_no: e.event_no, occurred_at: e.occurred_at, kind: e.kind, summary: e.summary }));
  return {
    version_no: r.version_no,
    audience: r.audience,
    generated_at: r.generated_at,
    judge_status: r.judge_status,
    withheld: r.withheld,
    body_markdown: r.body_markdown,
    footnotes: r.footnotes,
    mermaid_dsl: r.mermaid_dsl,
    node_evidence: r.node_evidence,
    flags: r.flags,
    timeline: r.withheld ? [] : timeline,
    excluded_summary: member.role === "manager" ? computeExcludedSummary(p) : null,
  };
}

// ---- AD-12（W25）：出来事の検索 ----

/** ラベル（"S1-2" 等）から、そのソース（"S1"）を引く。 */
function sourceForLabel(p: StoredProject, label: string): StoredSource | undefined {
  const sourceNo = Number.parseInt(label.slice(1).split("-")[0], 10);
  return p.sources.find((s) => s.source_no === sourceNo);
}

function segmentForLabel(p: StoredProject, label: string): StoredSegment | undefined {
  const source = sourceForLabel(p, label);
  return source?.segments.find((s) => s.label === label);
}

/**
 * 出来事の実効の可視性（design.md §5.5 の簡易版）。根拠のソースのどれかが除外なら「除外」、
 * どれかが managers_only、または partition = managers なら「managers_only」、それ以外は「all」。
 */
function eventEffectiveVisibility(p: StoredProject, e: StoredEvent): "all" | "managers_only" | "excluded" {
  let visibility: "all" | "managers_only" = e.partition === "managers" ? "managers_only" : "all";
  for (const label of e.segment_labels) {
    const source = sourceForLabel(p, label);
    if (!source) continue;
    if (source.is_excluded) return "excluded";
    if (source.visibility === "managers_only") visibility = "managers_only";
  }
  return visibility;
}

export function mockSearchEvents(pid: string, userId: string, query: string): EventSearchResult[] {
  const p = getProjectOr404(pid);
  const member = getMemberOr404(p, userId);
  const q = query.toLowerCase();
  const visible = p.events.filter((e) => {
    const visibility = eventEffectiveVisibility(p, e);
    if (member.role === "manager") return visibility !== "excluded";
    return visibility === "all";
  });
  const matched = visible.filter(
    (e) => e.summary.toLowerCase().includes(q) || (e.reason ?? "").toLowerCase().includes(q)
  );
  return matched
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
    .slice(0, 50)
    .map((e) => ({
      event_no: e.event_no,
      kind: e.kind,
      occurred_at: e.occurred_at,
      summary: e.summary,
      reason: e.reason,
      evidence: e.segment_labels.map((label) => ({
        label,
        text: segmentForLabel(p, label)?.text ?? "",
      })),
    }));
}

// ---- W20〜W23：通知・質問 ----

export function mockListNotifications(userId: string): NotificationItem[] {
  // モックでは通知は member 宛てのものだけ持たせている。プロジェクトの所属に関わらず本人宛てを返す。
  return notifications.filter((n) => {
    if (n.kind === "question") {
      const q = n.question_id ? questions.get(n.question_id) : null;
      return q?.asked_to === userId;
    }
    const p = projects.get(n.project_id);
    const member = p?.members.find((m) => m.user_id === userId);
    if (!member) return false;
    // AD-9：cost_limited はプロジェクトの manager にだけ届く。
    if (n.kind === "cost_limited") return member.role === "manager";
    // report・no_progress はデモとして全所属者に見せる（本物は宛先ごとに別レコード）。
    return true;
  });
}

export function mockMarkNotificationRead(nid: string, userId: string) {
  const n = notifications.find((x) => x.id === nid);
  if (!n) err(404, "not_found", "通知が見つかりません。");
  const visible = mockListNotifications(userId).some((x) => x.id === nid);
  if (!visible) err(404, "not_found", "通知が見つかりません。");
  n.read_at = now();
  return { ok: true as const };
}

export function mockGetQuestion(qid: string, userId: string): QuestionDetail {
  const q = questions.get(qid);
  if (!q || q.asked_to !== userId) err(404, "not_found", "質問が見つかりません。");
  return {
    question_id: q.question_id,
    project_id: q.project_id,
    question: q.question,
    status: q.status,
    related_event_summary: q.related_event_summary,
  };
}

export function mockAnswerQuestion(qid: string, userId: string, text: string) {
  const q = questions.get(qid);
  if (!q || q.asked_to !== userId) err(404, "not_found", "質問が見つかりません。");
  // worker（api/notifications.py answer_question）のエラーコードに合わせる。
  if (q.status !== "open") {
    err(409, "question_not_open", "この質問は既に回答済みか期限切れです。");
  }
  if (!text?.trim()) err(400, "invalid_input", "回答が空です。");
  const p = getProjectOr404(q.project_id);
  const sourceNo = p.nextSourceNo++;
  const source: StoredSource = {
    source_id: `s-${Math.random().toString(36).slice(2, 10)}`,
    project_id: p.project.id,
    source_no: sourceNo,
    type: "answer",
    filename: null,
    uploaded_by: userId,
    recorded_at: now(),
    visibility: "all",
    is_excluded: false,
    exclude_reason: null,
    current_version_no: 1,
    last_exclusion_change_by: null,
    visibility_audit: [],
    segments: [{ label: `S${sourceNo}-1`, seq: 1, speaker: "user", text }],
  };
  p.sources.push(source);
  q.status = "answered";
  return { source_id: source.source_id };
}
