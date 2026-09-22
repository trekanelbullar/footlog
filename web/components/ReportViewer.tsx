"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { compareIso, formatJst } from "@/lib/format";
import {
  ORIGIN_LABEL,
  isNewItem,
  parseArticle,
  pickHeadline,
  type ArticleItem,
  type ArticleSection,
} from "@/lib/article";
import {
  STATUS_LABEL,
  reportStatus,
  type ReportStatus,
} from "@/lib/report-status";
import Chip, { type ChipTone } from "@/components/ui/Chip";
import Button from "@/components/ui/Button";
import { TextField } from "@/components/ui/Field";
import { InlineError } from "@/components/ui/Feedback";
import type {
  EventKind,
  EventSearchResult,
  ExcludeReason,
  ReportDetail,
  SourceSummary,
} from "@/lib/worker-types";

// レポート画面の中央（文書ビュー）と右（根拠パネル）。design/ui-spec-3col.md・addenda AD-14。
// 本文の文は worker の Markdown を読み替えるだけで書き換えない。原文はテキストノードで出す（I2）。

const REASON_LABEL: Record<ExcludeReason, string> = {
  private: "個人的な内容",
  confidential: "機密情報",
  irrelevant: "無関係な内容",
  other: "その他",
};

const KIND_LABEL: Record<string, string> = {
  decision: "決定",
  rejected_option: "却下案",
  open_issue: "未解決",
  finding: "わかったこと",
  status: "現在地",
};

const SPEAKER_LABEL: Record<string, string> = {
  user: "人",
  ai: "AI",
  unknown: "不明",
};

const STATUS_TONE: Record<ReportStatus, ChipTone> = {
  checking: "gray",
  needs_review: "orange",
  checked: "green",
};

// 図の種類ごとの色（worker の DSL の形はそのまま、色だけ足す）。凡例と箇条書きの印にも使う。
const KIND_STYLE: Record<EventKind, { classDef: string; dot: string }> = {
  decision: {
    classDef: "fill:#111827,stroke:#111827,color:#ffffff",
    dot: "bg-gray-900",
  },
  rejected_option: {
    classDef: "fill:#ffffff,stroke:#9ca3af,color:#4b5563,stroke-dasharray:4 3",
    dot: "bg-gray-400",
  },
  open_issue: {
    classDef: "fill:#ffffff,stroke:#111827,color:#111827,stroke-width:2px",
    dot: "border-2 border-gray-900 bg-white",
  },
  finding: {
    classDef: "fill:#eef2ff,stroke:#c7d2fe,color:#1e1b4b",
    dot: "bg-indigo-200",
  },
  status: {
    classDef: "fill:#f9fafb,stroke:#d1d5db,color:#374151",
    dot: "bg-gray-200",
  },
};

const MERMAID_TIMEOUT_MS = 10000;
const MIN_QUERY_LENGTH = 2;

/** worker の DSL に種類ごとの色を足す。論点のレーンが無い版は横長（LR）にする。 */
function styledDsl(dsl: string, report: ReportDetail): string {
  const hasLanes = /^\s*subgraph T\d+/m.test(dsl);
  const base = hasLanes ? dsl : dsl.replace(/^flowchart TD\b/, "flowchart LR");
  const present = new Set(
    [...base.matchAll(/^\s*(E\d+)[[({>/]/gm)].map((m) => m[1]),
  );
  const byKind = new Map<EventKind, string[]>();
  for (const t of report.timeline) {
    const id = `E${t.event_no}`;
    if (present.has(id))
      byKind.set(t.kind, [...(byKind.get(t.kind) ?? []), id]);
  }
  const lines = [base.trimEnd()];
  for (const m of base.matchAll(/^\s*subgraph (T\d+)/gm)) {
    lines.push(`  style ${m[1]} fill:#ffffff,stroke:#e5e7eb,color:#374151`);
  }
  for (const [kind, ids] of byKind) {
    lines.push(`  classDef k_${kind} ${KIND_STYLE[kind].classDef}`);
    lines.push(`  class ${ids.join(",")} k_${kind}`);
  }
  return lines.join("\n");
}

/** ソースの種別（アイコンの文字と名前）。ファイルは拡張子で見分ける。 */
function sourceKind(source: SourceSummary | undefined): {
  icon: string;
  label: string;
  tone: string;
} {
  if (!source)
    return { icon: "?", label: "不明", tone: "bg-gray-100 text-gray-600" };
  if (source.type === "conversation")
    return { icon: "会", label: "会話", tone: "bg-indigo-50 text-indigo-700" };
  if (source.type === "answer")
    return { icon: "答", label: "回答", tone: "bg-indigo-50 text-indigo-700" };
  const ext = (source.filename ?? "").split(".").pop()?.toLowerCase() ?? "";
  if (ext === "pdf")
    return { icon: "PDF", label: "PDF", tone: "bg-red-50 text-red-700" };
  if (ext === "xlsx" || ext === "csv")
    return { icon: "XLS", label: "Excel", tone: "bg-green-50 text-green-700" };
  if (ext === "docx")
    return { icon: "DOC", label: "Word", tone: "bg-blue-50 text-blue-700" };
  if (ext === "pptx")
    return {
      icon: "PPT",
      label: "PowerPoint",
      tone: "bg-orange-50 text-orange-700",
    };
  return { icon: "TXT", label: "テキスト", tone: "bg-gray-100 text-gray-700" };
}

function sourceName(
  source: SourceSummary | undefined,
  sourceNo: number,
): string {
  if (!source) return `S${sourceNo}`;
  if (source.filename) return `S${sourceNo}　${source.filename}`;
  return `S${sourceNo}　${source.type === "answer" ? "質問への回答" : "会話"}`;
}

// ---- 根拠パネルの中身 ----------------------------------------------------------

interface QuoteCard {
  label: string;
  sourceNo: number | null;
  text: string;
  speaker?: string | null;
  recordedAt?: string | null;
}

interface PanelContent {
  title: string;
  cards: QuoteCard[];
}

function sourceNoOfLabel(label: string): number | null {
  const m = label.match(/^S(\d+)-/);
  return m ? Number(m[1]) : null;
}

function OriginTag({ item }: { item: ArticleItem }) {
  if (!item.origin) return null;
  return (
    <Chip tone={item.origin === "ai_unverified" ? "amber" : "gray"}>
      {ORIGIN_LABEL[item.origin]}
    </Chip>
  );
}

function SummaryItem({
  item,
  isNew,
  onFootnote,
  onNode,
}: {
  item: ArticleItem;
  isNew: boolean;
  onFootnote: (n: number) => void;
  onNode: (nodeId: string) => void;
}) {
  return (
    <li className="text-[15px] leading-8 text-[#2b2823]">
      {isNew && (
        <span className="mr-2 align-middle">
          <Chip tone="green">NEW</Chip>
        </span>
      )}
      {item.text}
      {item.unresolvedReason && (
        <span className="ml-1 text-sm text-amber-700">
          （理由は未確認：担当者に確認中）
        </span>
      )}
      <span className="ml-2 inline-flex flex-wrap gap-1 align-middle">
        <OriginTag item={item} />
        {item.similarRejected.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => onNode(id)}
            className="rounded-full border border-red-300 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700 hover:bg-red-100 focus-visible:outline-2 focus-visible:outline-red-700"
          >
            過去の却下案 {id} と類似
          </button>
        ))}
      </span>
      {item.footnotes.map((n) => (
        <button
          key={n}
          type="button"
          onClick={() => onFootnote(n)}
          aria-label={`脚注${n}の根拠を開く`}
          className="ml-1 align-super text-[11px] font-bold text-[#b4541a] hover:underline focus-visible:outline-2 focus-visible:outline-[#b4541a]"
        >
          [{n}]
        </button>
      ))}
    </li>
  );
}

function PanelSearch({
  pid,
  onOpen,
}: {
  pid: string;
  onOpen: (r: EventSearchResult) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<EventSearchResult[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "error">(
    "idle",
  );
  const [error, setError] = useState<string | null>(null);
  const q = query.trim();

  useEffect(() => {
    if (q.length < MIN_QUERY_LENGTH) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setStatus("loading");
      try {
        const res = await fetch(
          `/api/projects/${pid}/events/search?q=${encodeURIComponent(q)}`,
          {
            signal: controller.signal,
          },
        );
        const data = await res.json();
        if (!res.ok) {
          setError(data.message ?? "検索に失敗しました。");
          setStatus("error");
          return;
        }
        setResults(data as EventSearchResult[]);
        setStatus("ok");
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setError("通信に失敗しました。");
        setStatus("error");
      }
    }, 300);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [pid, q]);

  return (
    <div>
      <TextField
        label="出来事を検索"
        hideLabel
        type="search"
        placeholder="出来事を検索（2文字以上）"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {q.length >= MIN_QUERY_LENGTH && (
        <div className="mt-2">
          {status === "loading" && (
            <p className="text-sm text-gray-500">検索しています…</p>
          )}
          {status === "error" && <InlineError>{error}</InlineError>}
          {status === "ok" && results.length === 0 && (
            <p className="text-sm text-gray-500">見つかりませんでした。</p>
          )}
          {status === "ok" && results.length > 0 && (
            <ul className="max-h-64 divide-y divide-gray-100 overflow-y-auto rounded-md border border-gray-200">
              {results.map((r) => (
                <li key={r.event_no}>
                  <button
                    type="button"
                    onClick={() => onOpen(r)}
                    className="w-full px-3 py-2 text-left hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none"
                  >
                    <span className="text-xs text-gray-500">
                      {KIND_LABEL[r.kind] ?? r.kind} ・{" "}
                      {formatJst(r.occurred_at).slice(0, 10)}
                    </span>
                    <span className="block text-sm text-gray-900">
                      {r.summary}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function EvidencePanel({
  pid,
  content,
  about,
  sourcesByNo,
  onClose,
  onSearchOpen,
}: {
  pid: string;
  content: PanelContent | null;
  about: [string, React.ReactNode][];
  sourcesByNo: Map<number, SourceSummary>;
  onClose: () => void;
  onSearchOpen: (r: EventSearchResult) => void;
}) {
  const quotes = !content ? (
    <p className="text-xs leading-5 text-[#8a8175]">
      脚注の番号や図の出来事を押すと、根拠の原文がここに表示されます。原本は中央の「原本ソース」や「資料」タブからも開けます。
    </p>
  ) : content.cards.length === 0 ? (
    <p className="text-sm text-[#8a8175]">根拠が見つかりませんでした。</p>
  ) : (
    <ul className="flex flex-col gap-3">
      {content.cards.map((c, i) => {
        const source =
          c.sourceNo !== null ? sourcesByNo.get(c.sourceNo) : undefined;
        const when = c.recordedAt ?? source?.recorded_at ?? null;
        return (
          <li
            key={`${c.label}-${i}`}
            className="rounded-lg bg-[#efebe4] px-4 py-3"
          >
            <p className="text-[11px] font-semibold text-[#b4541a]">
              {c.sourceNo !== null ? sourceName(source, c.sourceNo) : c.label}
              {when ? ` ・ ${formatJst(when)}` : ""}
            </p>
            {/* 原文は Markdown として解釈せず、テキストノードのまま一字一句表示する（I2）。 */}
            <p className="mt-1.5 whitespace-pre-wrap break-words text-sm leading-6 text-[#2b2823]">
              「{c.text}」
            </p>
            <p className="mt-1.5 text-[11px] text-[#8a8175]">
              {c.label}
              {c.speaker
                ? ` ・ 話者 ${SPEAKER_LABEL[c.speaker] ?? c.speaker}`
                : ""}
            </p>
          </li>
        );
      })}
    </ul>
  );

  const aboutList = (
    <section className="border-t border-[#e6e1d8] pt-5">
      <h2 className="mb-3 text-xs font-semibold text-[#5b554c]">
        この記録について
      </h2>
      <dl className="flex flex-col gap-2 text-xs">
        {about.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between gap-3">
            <dt className="text-[#5b554c]">{k}</dt>
            <dd className="text-right text-[#2b2823]">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );

  const title = (
    <div className="mb-2 flex items-start justify-between gap-2">
      <h2 className="text-sm font-semibold text-[#2b2823]">
        {content ? content.title : "根拠"}
      </h2>
      {content && (
        <Button variant="ghost" onClick={onClose} aria-label="根拠を閉じる">
          ✕
        </Button>
      )}
    </div>
  );

  return (
    <>
      {/* PC：右カラムに固定 */}
      <aside
        aria-label="根拠パネル"
        className="hidden flex-col gap-6 border-l border-[#e6e1d8] bg-[#fbfaf7] px-6 py-6 lg:flex"
      >
        <div className="sticky top-4 flex max-h-[calc(100vh-2rem)] flex-col gap-6 overflow-y-auto">
          <div>
            <p className="mb-2 text-xs font-semibold text-[#5b554c]">
              出来事を検索
            </p>
            <PanelSearch pid={pid} onOpen={onSearchOpen} />
          </div>
          <section aria-live="polite">
            {title}
            {quotes}
          </section>
          {aboutList}
        </div>
      </aside>
      {/* 1024px 未満：選んだときだけ下から出るシート */}
      {content && (
        <div className="fixed inset-x-0 bottom-0 z-30 max-h-[70vh] overflow-y-auto rounded-t-xl border-t border-[#e6e1d8] bg-[#fbfaf7] p-4 lg:hidden">
          {title}
          {quotes}
        </div>
      )}
    </>
  );
}

// ---- 本体 --------------------------------------------------------------------

export default function ReportViewer({
  pid,
  report,
  projectName,
  newEventNos,
  sources,
  running = false,
}: {
  pid: string;
  report: ReportDetail;
  projectName: string;
  newEventNos: number[];
  sources: SourceSummary[];
  /** この版の後の実行が進行中か（ステータス「検査中」）。 */
  running?: boolean;
}) {
  const [diagramState, setDiagramState] = useState<"loading" | "ok" | "failed">(
    () => (report.mermaid_dsl?.trim() ? "loading" : "failed"),
  );
  const [svg, setSvg] = useState<string>("");
  const [panel, setPanel] = useState<PanelContent | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const fresh = useMemo(() => new Set(newEventNos), [newEventNos]);
  const sections = useMemo(
    () => parseArticle(report.body_markdown),
    [report.body_markdown],
  );
  const headline = useMemo(
    () => pickHeadline(report.timeline, fresh),
    [report.timeline, fresh],
  );
  const sourcesByNo = useMemo(
    () => new Map(sources.map((s) => [s.source_no, s])),
    [sources],
  );

  function openFootnote(n: number) {
    setPanel({
      title: `脚注 [${n}]`,
      cards: (report.footnotes[String(n)] ?? []).map((e) => ({
        label: e.label,
        sourceNo: e.source_no,
        text: e.text,
        speaker: e.speaker,
        recordedAt: e.recorded_at,
      })),
    });
  }

  function openNode(nodeId: string) {
    const t = report.timeline.find((x) => `E${x.event_no}` === nodeId);
    setPanel({
      title: t
        ? `${KIND_LABEL[t.kind] ?? t.kind}：${t.summary}`
        : `出来事 ${nodeId}`,
      cards: (report.node_evidence[nodeId] ?? []).map((e) => ({
        label: e.label,
        sourceNo: sourceNoOfLabel(e.label),
        text: e.text,
      })),
    });
  }

  function openSearchResult(r: EventSearchResult) {
    setPanel({
      title: `${KIND_LABEL[r.kind] ?? r.kind}：${r.summary}`,
      cards: r.evidence.map((e) => ({
        label: e.label,
        sourceNo: sourceNoOfLabel(e.label),
        text: e.text,
      })),
    });
  }

  useEffect(() => {
    const dsl = report.mermaid_dsl;
    if (!dsl?.trim()) return;
    let cancelled = false;
    const timeoutId = setTimeout(() => {
      if (!cancelled) setDiagramState("failed");
    }, MERMAID_TIMEOUT_MS);
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          // 描けなかったときに Mermaid が本文の末尾へ出すエラー図を出さない（箇条書きに切り替える）
          suppressErrorRendering: true,
          theme: "base",
          themeVariables: {
            fontFamily: "var(--font-noto-sans-jp), sans-serif",
            fontSize: "13px",
            lineColor: "#6b7280",
            primaryColor: "#ffffff",
            primaryTextColor: "#111827",
            primaryBorderColor: "#d1d5db",
          },
          // 横に長い時系列は枠に合わせて縮めると読めないため、元の大きさで描いて横にスクロールさせる。
          flowchart: { useMaxWidth: false },
        });
        const id = `mermaid-v${report.version_no}-${Math.random().toString(36).slice(2, 8)}`;
        const result = await mermaid.render(id, styledDsl(dsl, report));
        clearTimeout(timeoutId);
        if (!cancelled) {
          setSvg(result.svg);
          setDiagramState("ok");
        }
      } catch {
        clearTimeout(timeoutId);
        if (!cancelled) setDiagramState("failed");
      }
    })();
    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
    // report 全体ではなく、図に効く値だけで描き直す
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report.mermaid_dsl, report.version_no]);

  useEffect(() => {
    if (diagramState !== "ok" || !containerRef.current) return;
    const container = containerRef.current;
    const scroller = container.parentElement;
    if (scroller) scroller.scrollLeft = scroller.scrollWidth; // 最新（右端）から見せる
    const cleanups: (() => void)[] = [];
    for (const nodeId of Object.keys(report.node_evidence)) {
      container.querySelectorAll(`[id*="${nodeId}"]`).forEach((el) => {
        // E1 が E12 にも当たらないよう、id の末尾の区切りまで確かめる。
        if (!new RegExp(`(^|-)${nodeId}(-|$)`).test(el.id)) return;
        const handler = () => openNode(nodeId);
        el.addEventListener("click", handler);
        (el as HTMLElement).style.cursor = "pointer";
        cleanups.push(() => el.removeEventListener("click", handler));
      });
    }
    return () => cleanups.forEach((c) => c());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [diagramState, report.node_evidence]);

  if (report.withheld) {
    return (
      <div className="rounded-md border border-gray-200 px-6 py-10 text-sm text-gray-700">
        この版には非公開になった情報が含まれるため表示できません。次の実行で作り直されます。
      </div>
    );
  }

  const suspectedInjection = report.flags.suspected_injection ?? [];
  const unverifiedAiCount = report.flags.unverified_ai_count ?? 0;
  const rejectedSimilarity = report.flags.rejected_similarity ?? [];
  const status = reportStatus(report, running);
  const statusSection = sections.find((s) => s.kind === "status");
  const lead = statusSection?.items[0]?.text ?? null;

  // この版の根拠になったソース（脚注が引いたもの）を種類別に数える。
  const citedSourceNos = [
    ...new Set(
      Object.values(report.footnotes).flatMap((entries) =>
        entries.map((e) => e.source_no),
      ),
    ),
  ];
  const cited = citedSourceNos.map((n) => sourcesByNo.get(n));
  const logCount = cited.filter((s) => s && s.type !== "file").length;
  const docCount = cited.filter((s) => s && s.type === "file").length;

  const sortedTimeline = [...report.timeline].sort((a, b) =>
    compareIso(a.occurred_at, b.occurred_at),
  );
  const topics = report.flags.topics ?? [];
  const inTopic = new Set(topics.flatMap((t) => t.event_nos));
  const listGroups: { name: string | null; items: typeof sortedTimeline }[] =
    topics.length > 1
      ? [
          ...topics.map((t) => ({
            name: t.name,
            items: sortedTimeline.filter((i) =>
              t.event_nos.includes(i.event_no),
            ),
          })),
          {
            name: "その他",
            items: sortedTimeline.filter((i) => !inTopic.has(i.event_no)),
          },
        ].filter((g) => g.items.length > 0)
      : [{ name: null, items: sortedTimeline }];

  const about: [string, React.ReactNode][] = [
    ["生成元", `会話ログ ${logCount}件・資料 ${docCount}件`],
    ["最終更新", formatJst(report.generated_at)],
    [
      "ステータス",
      <Chip key="s" tone={STATUS_TONE[status]}>
        {STATUS_LABEL[status]}
      </Chip>,
    ],
    ["版", `第${report.version_no}版`],
    ["読者", report.audience === "managers" ? "マネージャー" : "全員"],
    ["未確認", `${unverifiedAiCount}件`],
    ["出来事", `${report.timeline.length}件`],
  ];

  const renderSection = (section: ArticleSection) => (
    <section
      key={section.heading}
      className="border-t border-[#e6e1d8] pt-6 first:border-t-0 first:pt-0"
    >
      {section.kind === "rejected" && rejectedSimilarity.length > 0 && (
        <div className="mb-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          <span className="font-semibold">再浮上の警告：</span>
          {rejectedSimilarity.map((f, i) => (
            <button
              key={`${f.event_no}-${f.rejected_event_no}`}
              type="button"
              onClick={() => openNode(`E${f.rejected_event_no}`)}
              className="underline underline-offset-2 hover:text-red-950"
            >
              {i > 0 ? "、" : ""}E{f.event_no} は過去に却下した E
              {f.rejected_event_no} と似ています
            </button>
          ))}
        </div>
      )}
      <p className="text-[11px] font-bold tracking-[0.15em] text-[#b4541a]">
        {section.label}
      </p>
      <h3 className="mt-1 mb-3 text-lg font-bold text-[#2b2823]">
        {section.heading}
      </h3>
      {section.items.length === 0 ? (
        <p className="text-sm text-[#8a8175]">該当する出来事はありません。</p>
      ) : (
        <ul className="flex list-disc flex-col gap-2 pl-5 marker:text-[#b8afa2]">
          {section.items.map((item, i) => (
            <SummaryItem
              key={i}
              item={item}
              isNew={isNewItem(item, report, fresh)}
              onFootnote={openFootnote}
              onNode={openNode}
            />
          ))}
        </ul>
      )}
    </section>
  );

  return (
    <div className="grid gap-8 lg:-mr-8 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-10">
      <article className="min-w-0 text-[#2b2823]">
        {/* パンくず・メタデータ・タイトル（ui-spec-3col.md 2・3） */}
        <header className="pb-2">
          <nav aria-label="パンくず" className="text-xs text-[#8a8175]">
            決定ログ <span className="mx-2 text-[#c9c1b5]">/</span>{" "}
            {projectName}
          </nav>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Chip tone="gray">
              第{report.version_no}版
              {report.audience === "managers" ? "・管理者向け" : ""}
            </Chip>
            <Chip tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Chip>
            {fresh.size > 0 && <Chip tone="green">NEW {fresh.size}件</Chip>}
            <span className="ml-auto text-xs text-[#8a8175]">
              {formatJst(report.generated_at)} 更新
            </span>
          </div>
          <h1 className="mt-5 max-w-[720px] break-words text-[28px] font-bold leading-[1.45] text-[#2b2823] md:text-[32px]">
            {headline ? headline.summary : `${projectName} の記録`}
          </h1>
          {lead && (
            <p className="mt-3 max-w-[720px] text-sm leading-relaxed text-[#5b554c]">
              {lead}
            </p>
          )}
        </header>

        {suspectedInjection.length > 0 && (
          <p className="mt-6 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            <span className="font-semibold">誘導の疑いのある記述を検出</span>
            　対象：{suspectedInjection.join("、")}
          </p>
        )}

        {/* AI要約ブロック（ui-spec-3col.md 4）：AIの印とラベルを必ず付け、本文を字下げして地の文と分ける */}
        <section aria-label="AIによる自動要約" className="mt-6 flex gap-3">
          <span
            aria-hidden
            className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-[#1f1d1a] text-white"
          >
            <svg viewBox="0 0 16 16" className="size-3.5 fill-current">
              <path d="M8 1l1.6 3.9L13.5 6 9.6 7.6 8 11.5 6.4 7.6 2.5 6l3.9-1.1L8 1zm4.5 8l.8 1.9 1.7.6-1.7.7-.8 1.8-.8-1.8-1.7-.7 1.7-.6.8-1.9z" />
            </svg>
          </span>
          <div className="min-w-0 flex-1">
            <p className="mb-4 text-[11px] font-semibold text-[#8a8175]">
              AIによる自動要約 — {logCount}件の会話ログと{docCount}
              件の資料から生成
            </p>
            <div className="flex max-w-[720px] flex-col gap-6">
              {sections.map(renderSection)}
            </div>
          </div>
        </section>

        {/* 図（タイムライン） */}
        <figure className="mt-10 border-t border-[#e6e1d8] pt-8">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-base font-semibold">決定の流れ</h2>
            <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-600">
              {(Object.keys(KIND_STYLE) as EventKind[]).map((kind) => (
                <li key={kind} className="inline-flex items-center gap-1">
                  <span
                    className={`inline-block size-2.5 rounded-sm ${KIND_STYLE[kind].dot}`}
                  />
                  {KIND_LABEL[kind]}
                </li>
              ))}
            </ul>
          </div>
          {diagramState !== "failed" ? (
            <div className="overflow-x-auto rounded-lg border border-[#e6e1d8] bg-white p-3">
              {diagramState === "loading" && (
                <p className="py-8 text-sm text-gray-500">
                  図を描画しています…
                </p>
              )}
              {diagramState === "ok" && (
                // mermaid.render の出力（securityLevel: "strict"）を描画する。
                <div
                  ref={containerRef}
                  className="w-max"
                  dangerouslySetInnerHTML={{ __html: svg }}
                />
              )}
            </div>
          ) : report.timeline.length === 0 ? (
            <p className="text-sm text-gray-500">
              表示できる出来事がありません。
            </p>
          ) : (
            <div className="rounded-md border border-gray-200">
              {listGroups.map((group) => (
                <div key={group.name ?? "all"}>
                  {group.name && (
                    <p className="border-b border-gray-200 bg-gray-50 px-3 py-1.5 text-xs font-semibold text-gray-700">
                      {group.name}
                    </p>
                  )}
                  <ol className="divide-y divide-gray-100">
                    {group.items.map((item) => (
                      <li key={item.event_no}>
                        <button
                          type="button"
                          onClick={() => openNode(`E${item.event_no}`)}
                          className="flex w-full items-start gap-3 px-3 py-2 text-left hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none"
                        >
                          <span
                            className={`mt-1.5 inline-block size-2.5 shrink-0 rounded-sm ${KIND_STYLE[item.kind]?.dot ?? "bg-gray-300"}`}
                          />
                          <span className="min-w-0">
                            <span className="text-xs text-gray-500">
                              {formatJst(item.occurred_at)} ・{" "}
                              {KIND_LABEL[item.kind] ?? item.kind}
                              {fresh.has(item.event_no) && (
                                <span className="ml-2 font-semibold text-green-700">
                                  NEW
                                </span>
                              )}
                            </span>
                            <span className="block text-sm text-gray-900">
                              {item.summary}
                            </span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
            </div>
          )}
          <figcaption className="mt-2 text-xs text-gray-500">
            この版までの出来事（{report.timeline.length}件）を、
            {topics.length > 1
              ? "論点ごとの段に分け、各段の中で左から古い順に"
              : "左から古い順に"}
            並べたもの。出来事を押すと、右に根拠の原文が出る。
          </figcaption>
        </figure>

        {/* 原本ソース一覧（ui-spec-3col.md 5） */}
        <section className="mt-10 border-t border-[#e6e1d8] pt-8">
          <h2 className="mb-3 text-sm font-semibold text-[#5b554c]">
            原本ソース（{sources.length}件）
          </h2>
          {sources.length === 0 ? (
            <p className="text-sm text-gray-500">資料はまだありません。</p>
          ) : (
            <ul className="flex max-w-[520px] flex-col gap-2">
              {sources.map((s) => {
                const kind = sourceKind(s);
                return (
                  <li key={s.source_id}>
                    <Link
                      href={`/projects/${pid}/sources#source-${s.source_id}`}
                      className="flex items-center gap-3 rounded-lg border border-[#e2ddd3] bg-white px-3 py-2.5 hover:border-[#c9c1b5] focus-visible:outline-2 focus-visible:outline-[#b4541a]"
                    >
                      <span
                        className={`inline-flex h-7 min-w-9 items-center justify-center rounded px-1 text-[10px] font-bold ${kind.tone}`}
                      >
                        {kind.icon}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-[#2b2823]">
                        {sourceName(s, s.source_no)}
                      </span>
                      <span className="shrink-0 text-xs text-[#8a8175]">
                        {s.is_excluded ? "除外中・" : ""}
                        {formatJst(s.recorded_at).slice(0, 10)}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
          {/* 除外の件数と内訳は manager にだけ届く（worker が member には null を返す） */}
          {report.excluded_summary && (
            <p className="mt-3 text-xs text-[#8a8175]">
              除外されたソース {report.excluded_summary.count}件（
              {(Object.keys(REASON_LABEL) as ExcludeReason[])
                .map(
                  (r) =>
                    `${REASON_LABEL[r]} ${report.excluded_summary!.by_reason[r] ?? 0}`,
                )
                .join("・")}
              ）
            </p>
          )}
        </section>
      </article>

      <EvidencePanel
        pid={pid}
        content={panel}
        about={about}
        sourcesByNo={sourcesByNo}
        onClose={() => setPanel(null)}
        onSearchOpen={openSearchResult}
      />
    </div>
  );
}
