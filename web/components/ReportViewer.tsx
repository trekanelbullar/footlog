"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import Button from "@/components/ui/Button";
import { compareIso, formatJst } from "@/lib/format";
import {
  ORIGIN_LABEL,
  isNewItem,
  parseArticle,
  pickHeadline,
  type ArticleItem,
  type ArticleSection,
} from "@/lib/article";
import type {
  EventKind,
  ExcludeReason,
  NodeEvidenceEntry,
  ReportDetail,
} from "@/lib/worker-types";

// レポート1本＝記事1本の体裁（2026-09-22 ユーザー指定）。カード・影・グラデーションは使わず、
// 黒＋濃い赤、線と余白で区切る。本文の文は worker の Markdown を読み替えるだけで書き換えない。

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

// 図の形（worker の DSL）に合わせた、種類ごとの色。凡例と箇条書きの印にも同じものを使う。
const KIND_STYLE: Record<
  EventKind,
  { classDef: string; glyph: string; glyphClass: string }
> = {
  decision: {
    classDef: "fill:#141414,stroke:#141414,color:#fbf8f1",
    glyph: "■",
    glyphClass: "text-ink",
  },
  rejected_option: {
    classDef: "fill:#fbf8f1,stroke:#9b1c1c,color:#9b1c1c,stroke-dasharray:4 3",
    glyph: "⬡",
    glyphClass: "text-accent",
  },
  open_issue: {
    classDef: "fill:#fbf8f1,stroke:#141414,color:#141414,stroke-width:2px",
    glyph: "▱",
    glyphClass: "text-ink",
  },
  finding: {
    classDef: "fill:#ece5d6,stroke:#ece5d6,color:#141414",
    glyph: "●",
    glyphClass: "text-[#8a8375]",
  },
  status: {
    classDef: "fill:#fbf8f1,stroke:#8a8375,color:#4a463f",
    glyph: "▶",
    glyphClass: "text-[#8a8375]",
  },
};

const MERMAID_TIMEOUT_MS = 10000;

// 根拠は右パネルに出す（本文をスクロールさせて飛ばさない、design/ui-guidelines.md a）。
interface PanelEntry {
  label: string;
  text: string;
  meta?: string;
}
interface PanelContent {
  title: string;
  entries: PanelEntry[];
}
const PanelContext = createContext<{
  footnote: (n: number) => void;
  node: (id: string) => void;
}>({
  footnote: () => {},
  node: () => {},
});

/** worker の DSL（flowchart TD）を、横長の時系列にして、種類ごとの色を付ける。 */
function styledDsl(dsl: string, report: ReportDetail): string {
  // 論点ごとのレーン（subgraph）がある版は、worker が縦に積んだ形（TD、各レーンの中は LR）のまま使う。
  const hasLanes = /^\s*subgraph T\d+/m.test(dsl);
  const lr = hasLanes ? dsl : dsl.replace(/^flowchart TD\b/, "flowchart LR");
  const present = new Set(
    [...lr.matchAll(/^\s*(E\d+)[[({>/]/gm)].map((m) => m[1]),
  );
  const byKind = new Map<EventKind, string[]>();
  for (const t of report.timeline) {
    const id = `E${t.event_no}`;
    if (!present.has(id)) continue;
    byKind.set(t.kind, [...(byKind.get(t.kind) ?? []), id]);
  }
  const lines = [lr.trimEnd()];
  for (const m of lr.matchAll(/^\s*subgraph (T\d+)/gm)) {
    lines.push(`  style ${m[1]} fill:#ffffff,stroke:#d9d3c7,color:#141414`);
  }
  for (const [kind, ids] of byKind) {
    lines.push(`  classDef k_${kind} ${KIND_STYLE[kind].classDef}`);
    lines.push(`  class ${ids.join(",")} k_${kind}`);
  }
  return lines.join("\n");
}

function Kicker({ children }: { children: ReactNode }) {
  return (
    <p className="font-sans-jp text-[11px] font-bold tracking-[0.25em] text-accent">
      {children}
    </p>
  );
}

function OriginTagView({ item }: { item: ArticleItem }) {
  if (!item.origin) return null;
  const amber = item.origin === "ai_unverified";
  return (
    <span
      className={`ml-2 inline-block border px-1.5 py-px align-middle text-[10px] font-medium tracking-wide ${
        amber
          ? "border-amber bg-[#fdf3dc] text-amber"
          : "border-rule text-[#6b655a]"
      }`}
    >
      {ORIGIN_LABEL[item.origin]}
    </span>
  );
}

function FootnoteRefs({ numbers }: { numbers: number[] }) {
  const panel = useContext(PanelContext);
  if (numbers.length === 0) return null;
  return (
    <sup className="ml-0.5">
      {numbers.map((n) => (
        <button
          key={n}
          type="button"
          onClick={() => panel.footnote(n)}
          className="mx-0.5 font-sans-jp text-[11px] font-bold text-accent hover:underline"
        >
          ［{n}］
        </button>
      ))}
    </sup>
  );
}

/** 根拠の原文。1件目は雑誌のプルクオートのように大きく、2件目以降は小さく添える（原文は一字一句そのまま、I2）。 */
function PullQuote({ n, report }: { n: number; report: ReportDetail }) {
  const entries = report.footnotes[String(n)] ?? [];
  if (entries.length === 0) return null;
  const [first, ...rest] = entries;
  const size =
    first.text.length <= 50
      ? "text-2xl md:text-3xl"
      : first.text.length <= 120
        ? "text-xl md:text-2xl"
        : "text-lg";
  return (
    <figure
      id={`fn-${n}`}
      className="my-8 scroll-mt-24 border-l-4 border-accent py-1 pl-5 md:pl-7"
    >
      <blockquote
        className={`whitespace-pre-wrap break-words font-serif-jp font-bold leading-snug text-ink ${size}`}
      >
        {first.text}
      </blockquote>
      <figcaption className="mt-3 font-sans-jp text-xs text-[#6b655a]">
        <span className="font-bold text-accent">［{n}］</span> 原文{" "}
        {first.label}・S{first.source_no}・{first.speaker ?? "不明"}・
        {formatJst(first.recorded_at)}
      </figcaption>
      {rest.map((entry, i) => (
        <div
          key={`${entry.label}-${i}`}
          className="mt-3 border-t border-rule pt-2"
        >
          <p className="whitespace-pre-wrap break-words font-sans-jp text-sm text-ink">
            {entry.text}
          </p>
          <p className="mt-1 font-sans-jp text-[11px] text-[#6b655a]">
            原文 {entry.label}・S{entry.source_no}・{entry.speaker ?? "不明"}・
            {formatJst(entry.recorded_at)}
          </p>
        </div>
      ))}
    </figure>
  );
}

function ArticleParagraph({
  item,
  isNew,
  quotes,
  report,
}: {
  item: ArticleItem;
  isNew: boolean;
  quotes: number[];
  report: ReportDetail;
}) {
  const panel = useContext(PanelContext);
  return (
    <>
      <p className="font-sans-jp text-[17px] leading-[1.95] text-ink">
        {isNew && (
          <span className="mr-2 inline-block bg-accent px-1.5 py-px align-middle text-[10px] font-bold tracking-widest text-white">
            NEW
          </span>
        )}
        {item.text}
        {item.unresolvedReason && (
          <span className="ml-1 text-sm text-amber">
            （理由は未確認：担当者に確認中）
          </span>
        )}
        <OriginTagView item={item} />
        {item.similarRejected.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => panel.node(id)}
            className="ml-2 inline-block border border-accent px-1.5 py-px align-middle text-[10px] font-bold text-accent"
          >
            過去の却下案 {id} と類似
          </button>
        ))}
        <FootnoteRefs numbers={item.footnotes} />
      </p>
      {quotes.map((n) => (
        <PullQuote key={n} n={n} report={report} />
      ))}
    </>
  );
}

function EvidenceQuotes({ items }: { items: NodeEvidenceEntry[] }) {
  if (items.length === 0)
    return (
      <p className="font-sans-jp text-sm text-[#6b655a]">
        根拠が見つかりませんでした。
      </p>
    );
  return (
    <>
      {items.map((item, i) => (
        <blockquote key={`${item.label}-${i}`} className="mb-3 last:mb-0">
          {/* 原文は Markdown として解釈せず、テキストノードのまま一字一句表示する（I2）。 */}
          <p className="whitespace-pre-wrap break-words font-serif-jp text-lg font-bold leading-snug text-ink">
            {item.text}
          </p>
          <p className="mt-1 font-sans-jp text-[11px] text-[#6b655a]">
            原文 {item.label}
          </p>
        </blockquote>
      ))}
    </>
  );
}

export default function ReportViewer({
  report,
  projectName,
  newEventNos,
}: {
  report: ReportDetail;
  projectName: string;
  newEventNos: number[];
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

  // 図のノード（またはその代わりの箇条書き）を押したとき：同じ原文を引く脚注の引用へ移動する。
  // 本文で引いていない出来事なら、図の下の欄に根拠を出してそこへ移動する。
  function openNode(nodeId: string) {
    const t = report.timeline.find((x) => `E${x.event_no}` === nodeId);
    setPanel({
      title: t
        ? `${KIND_LABEL[t.kind] ?? t.kind}：${t.summary}`
        : `出来事 ${nodeId}`,
      entries: (report.node_evidence[nodeId] ?? []).map((e) => ({
        label: e.label,
        text: e.text,
      })),
    });
  }

  function openFootnote(n: number) {
    setPanel({
      title: `脚注［${n}］`,
      entries: (report.footnotes[String(n)] ?? []).map((e) => ({
        label: e.label,
        text: e.text,
        meta: `S${e.source_no}・${e.speaker ?? "不明"}・${formatJst(e.recorded_at)}`,
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
        // 横に長い時系列は枠に合わせて縮めると読めないため、元の大きさで描いて横にスクロールさせる。
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          // 描けなかったときに Mermaid が本文の末尾へ出すエラー図を出さない（箇条書きに切り替える）
          suppressErrorRendering: true,
          theme: "base",
          themeVariables: {
            fontFamily: "var(--font-noto-sans-jp), sans-serif",
            fontSize: "14px",
            lineColor: "#141414",
            primaryColor: "#fbf8f1",
            primaryTextColor: "#141414",
            primaryBorderColor: "#141414",
          },
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
    // 最新（右端）から見せる。
    const scroller = container.parentElement;
    if (scroller) scroller.scrollLeft = scroller.scrollWidth;
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
      <div className="border-y-2 border-ink bg-paper px-6 py-10 font-sans-jp text-sm text-ink">
        この版には非公開になった情報が含まれるため表示できません。次の実行で作り直されます。
      </div>
    );
  }

  const suspectedInjection = report.flags.suspected_injection ?? [];
  const unverifiedAiCount = report.flags.unverified_ai_count ?? 0;
  const rejectedSimilarity = report.flags.rejected_similarity ?? [];

  const rejectedSection = sections.find((s) => s.kind === "rejected");
  const statusSection = sections.find((s) => s.kind === "status");
  const mainSections = sections.filter((s) => s.kind !== "rejected");
  const lead = statusSection?.items[0]?.text ?? null;

  // 各脚注の引用は、本文で最初に出てきた項目の直後に1度だけ置く。
  const quoted = new Set<number>();
  const quotesFor = (item: ArticleItem) => {
    const list = item.footnotes.filter((n) => !quoted.has(n));
    list.forEach((n) => quoted.add(n));
    return list;
  };

  const sortedTimeline = [...report.timeline].sort((a, b) =>
    compareIso(a.occurred_at, b.occurred_at),
  );
  const quoteCount = Object.keys(report.footnotes).length;

  // 図が描けないときの箇条書きも、図と同じく論点ごとに分ける（どこにも入らない出来事は「その他」）。
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

  const renderSection = (section: ArticleSection) => (
    <section key={section.heading} className="border-t border-ink pt-8">
      <Kicker>{section.label}</Kicker>
      <h2 className="mt-2 mb-6 font-serif-jp text-2xl font-black leading-tight text-ink md:text-3xl">
        {section.heading}
      </h2>
      {section.items.length === 0 ? (
        <p className="font-sans-jp text-sm text-[#6b655a]">
          該当する出来事はありません。
        </p>
      ) : (
        <div className="flex flex-col gap-5">
          {section.items.map((item, i) => (
            <ArticleParagraph
              key={i}
              item={item}
              isNew={isNewItem(item, report, fresh)}
              quotes={quotesFor(item)}
              report={report}
            />
          ))}
        </div>
      )}
    </section>
  );

  return (
    <PanelContext.Provider value={{ footnote: openFootnote, node: openNode }}>
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <article className="min-w-0 font-sans-jp text-ink">
          {/* ヒーロー */}
          <header className="border-b-2 border-ink pt-2 pb-8">
            <div className="mx-auto max-w-6xl">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <Kicker>決定ログ ／ {projectName}</Kicker>
              </div>
              <h1 className="mt-6 max-w-4xl break-words font-serif-jp text-[2rem] font-black leading-[1.25] text-ink md:text-6xl md:leading-[1.15]">
                {headline ? headline.summary : `${projectName} の記録`}
              </h1>
              {lead && (
                <p className="mt-6 max-w-2xl text-lg leading-relaxed text-[#3a3731]">
                  {lead}
                </p>
              )}
              <p className="mt-6 flex flex-wrap gap-x-5 gap-y-1 border-t border-rule pt-3 text-xs tracking-wide text-[#6b655a]">
                <span>{formatJst(report.generated_at)}</span>
                <span>
                  第{report.version_no}版
                  {report.audience === "managers" ? "（管理者向け）" : ""}
                </span>
                <span
                  className={
                    unverifiedAiCount > 0 ? "font-bold text-amber" : ""
                  }
                >
                  未確認 {unverifiedAiCount}件
                </span>
                <span
                  className={
                    report.judge_status === "pass"
                      ? ""
                      : "font-bold text-accent"
                  }
                >
                  検査 {report.judge_status === "pass" ? "合格" : "要確認"}
                </span>
                {fresh.size > 0 && (
                  <span className="font-bold text-accent">
                    NEW {fresh.size}件
                  </span>
                )}
              </p>
            </div>
          </header>

          {(suspectedInjection.length > 0 || report.excluded_summary) && (
            <div className="">
              {suspectedInjection.length > 0 && (
                <p className="mt-6 border-y border-accent py-2 text-sm text-accent">
                  <span className="font-bold">誘導の疑いのある記述を検出</span>
                  　対象：{suspectedInjection.join("、")}
                </p>
              )}
              {report.excluded_summary && (
                <p className="mt-4 text-xs text-[#6b655a]">
                  除外されたソース {report.excluded_summary.count}件（
                  {Object.entries(REASON_LABEL)
                    .map(
                      ([reason, label]) =>
                        `${label} ${report.excluded_summary!.by_reason[reason as ExcludeReason] ?? 0}`,
                    )
                    .join("・")}
                  ）
                </p>
              )}
            </div>
          )}

          <div className="flex max-w-[44rem] flex-col gap-12 py-12">
            {mainSections
              .filter(
                (s) =>
                  s.kind === "decision" ||
                  s.kind === "reason" ||
                  s.kind === "other",
              )
              .map(renderSection)}

            {/* 却下案の囲み記事 */}
            <aside className="border-2 border-ink bg-white">
              {rejectedSimilarity.length > 0 && (
                <div className="bg-accent px-5 py-2 text-sm font-bold text-white">
                  再浮上の警告：
                  {rejectedSimilarity.map((f, i) => (
                    <button
                      key={`${f.event_no}-${f.rejected_event_no}`}
                      type="button"
                      onClick={() => openNode(`E${f.rejected_event_no}`)}
                      className="underline decoration-white/60 underline-offset-2 hover:decoration-white"
                    >
                      {i > 0 ? "、" : ""}E{f.event_no} は過去に却下した E
                      {f.rejected_event_no} と似ています
                    </button>
                  ))}
                </div>
              )}
              <div className="px-5 py-6 md:px-7">
                <Kicker>REJECTED</Kicker>
                <h2 className="mt-2 mb-4 font-serif-jp text-xl font-black text-ink">
                  {rejectedSection?.heading ?? "検討したが採用しなかった選択肢"}
                </h2>
                {!rejectedSection || rejectedSection.items.length === 0 ? (
                  <p className="text-sm text-[#6b655a]">
                    この版で却下した案はありません。
                  </p>
                ) : (
                  <div className="flex flex-col gap-4">
                    {rejectedSection.items.map((item, i) => (
                      <ArticleParagraph
                        key={i}
                        item={item}
                        isNew={isNewItem(item, report, fresh)}
                        quotes={quotesFor(item)}
                        report={report}
                      />
                    ))}
                  </div>
                )}
                {rejectedSimilarity.length > 0 && (
                  <div className="mt-6 border-t border-rule pt-4">
                    <p className="mb-3 text-[11px] font-bold tracking-[0.2em] text-accent">
                      元の却下案
                    </p>
                    {[
                      ...new Set(
                        rejectedSimilarity.map((f) => f.rejected_event_no),
                      ),
                    ].map((no) => (
                      <div
                        key={no}
                        id={`ev-E${no}`}
                        className="mb-4 scroll-mt-24 border-l-4 border-accent pl-4"
                      >
                        <p className="mb-1 text-xs text-[#6b655a]">E{no}</p>
                        <EvidenceQuotes
                          items={report.node_evidence[`E${no}`] ?? []}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </aside>

            {mainSections
              .filter((s) => s.kind === "open" || s.kind === "status")
              .map(renderSection)}
          </div>

          {/* 図：ページ幅いっぱいのインフォグラフィック */}
          <figure className="border-y-2 border-ink">
            <div className="flex flex-wrap items-baseline justify-between gap-2 pt-6">
              <div>
                <Kicker>TIMELINE</Kicker>
                <p className="mt-1 font-serif-jp text-xl font-black">
                  決定の流れ
                </p>
              </div>
              <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#6b655a]">
                {(Object.keys(KIND_STYLE) as EventKind[]).map((kind) => (
                  <li key={kind}>
                    <span className={`mr-1 ${KIND_STYLE[kind].glyphClass}`}>
                      {KIND_STYLE[kind].glyph}
                    </span>
                    {KIND_LABEL[kind]}
                  </li>
                ))}
              </ul>
            </div>

            {diagramState !== "failed" ? (
              <div className="mt-4 overflow-x-auto pb-4">
                {diagramState === "loading" && (
                  <p className="py-10 text-sm text-[#6b655a]">
                    図を組んでいます…
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
              <p className="py-8 text-sm text-[#6b655a]">
                表示できる出来事がありません。
              </p>
            ) : (
              <div className="mt-4 pb-4">
                {listGroups.map((group) => (
                  <div key={group.name ?? "all"} className="mb-4 last:mb-0">
                    {group.name && (
                      <p className="border-b border-ink pb-1 text-xs font-bold tracking-[0.15em] text-ink">
                        {group.name}
                      </p>
                    )}
                    <ol>
                      {group.items.map((item) => (
                        <li
                          key={item.event_no}
                          className="border-t border-rule first:border-t-0"
                        >
                          <button
                            type="button"
                            onClick={() => openNode(`E${item.event_no}`)}
                            className="grid w-full grid-cols-[1.25rem_1fr] gap-x-3 py-3 text-left md:grid-cols-[1.25rem_9rem_1fr]"
                          >
                            <span
                              className={`${KIND_STYLE[item.kind]?.glyphClass ?? ""} text-lg leading-6`}
                            >
                              {KIND_STYLE[item.kind]?.glyph ?? "・"}
                            </span>
                            <span className="text-xs leading-6 text-[#6b655a] md:text-sm">
                              {formatJst(item.occurred_at)} ・{" "}
                              {KIND_LABEL[item.kind] ?? item.kind}
                              {fresh.has(item.event_no) && (
                                <span className="ml-2 font-bold text-accent">
                                  NEW
                                </span>
                              )}
                            </span>
                            <span className="col-start-2 text-[15px] leading-relaxed md:col-start-3">
                              {item.summary}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ol>
                  </div>
                ))}
              </div>
            )}

            <figcaption className="border-t border-rule py-3 text-xs leading-relaxed text-[#6b655a]">
              図　この版までの出来事（{report.timeline.length}件）を、
              {topics.length > 1
                ? "論点ごとの段に分け、各段の中で左から古い順に"
                : "左から古い順に"}
              並べたもの。
              形と色は出来事の種類を表す。項目を押すと、右に根拠の原文が出る。
            </figcaption>
          </figure>

          {/* 奥付 */}
          <footer className="py-8">
            <p className="mb-3 text-[11px] font-bold tracking-[0.25em] text-ink">
              奥付
            </p>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-[#6b655a] md:grid-cols-4">
              <div>
                <dt className="inline">版　</dt>
                <dd className="inline">第{report.version_no}版</dd>
              </div>
              <div>
                <dt className="inline">組版　</dt>
                <dd className="inline">{formatJst(report.generated_at)}</dd>
              </div>
              <div>
                <dt className="inline">読者　</dt>
                <dd className="inline">
                  {report.audience === "managers" ? "管理者" : "全員"}
                </dd>
              </div>
              <div>
                <dt className="inline">検査　</dt>
                <dd className="inline">
                  {report.judge_status === "pass" ? "合格" : "要確認"}
                </dd>
              </div>
              <div>
                <dt className="inline">引用　</dt>
                <dd className="inline">{quoteCount}件</dd>
              </div>
              <div>
                <dt className="inline">出来事　</dt>
                <dd className="inline">{report.timeline.length}件</dd>
              </div>
              <div>
                <dt className="inline">未確認　</dt>
                <dd className="inline">{unverifiedAiCount}件</dd>
              </div>
            </dl>
          </footer>
        </article>
        <EvidencePanel content={panel} onClose={() => setPanel(null)} />
      </div>
    </PanelContext.Provider>
  );
}

/** 右の根拠パネル。スマホ幅では、選んだときだけ画面下に出る。 */
function EvidencePanel({
  content,
  onClose,
}: {
  content: PanelContent | null;
  onClose: () => void;
}) {
  return (
    <aside
      className={`font-sans-jp lg:sticky lg:top-4 lg:block lg:self-start ${
        content
          ? "fixed inset-x-0 bottom-0 z-30 max-h-[60vh] overflow-y-auto border-t border-gray-300 bg-white p-4 lg:static lg:max-h-[calc(100vh-2rem)] lg:rounded-md lg:border"
          : "hidden rounded-md border border-gray-200 p-4 lg:block"
      }`}
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-gray-900">
          {content ? content.title : "根拠"}
        </p>
        {content && (
          <Button variant="ghost" onClick={onClose} aria-label="根拠を閉じる">
            ✕
          </Button>
        )}
      </div>
      {!content ? (
        <p className="text-sm text-gray-500">
          脚注の番号や図の出来事を押すと、根拠の原文がここに出ます。
        </p>
      ) : content.entries.length === 0 ? (
        <p className="text-sm text-gray-500">根拠が見つかりませんでした。</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {content.entries.map((e, i) => (
            <li
              key={`${e.label}-${i}`}
              className="border-l-2 border-gray-300 pl-3"
            >
              <p className="text-xs text-gray-500">
                {e.label}
                {e.meta ? `・${e.meta}` : ""}
              </p>
              {/* 原文は Markdown として解釈せず、テキストノードのまま一字一句表示する（I2）。 */}
              <p className="mt-1 whitespace-pre-wrap break-words text-sm text-gray-900">
                {e.text}
              </p>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
