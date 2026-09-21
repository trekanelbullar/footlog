"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { AnchorHTMLAttributes, HTMLAttributes, LiHTMLAttributes, OlHTMLAttributes } from "react";
import { compareIso, formatJst, linkFootnoteRefs } from "@/lib/format";
import type { ExcludeReason, NodeEvidenceEntry, ReportDetail } from "@/lib/worker-types";

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

const MERMAID_TIMEOUT_MS = 10000;

function SafeLink({ href, children }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  // 脚注の印（linkFootnoteRefs が作る #fn-n）だけは、ページ内リンクとして上付きで出す。
  if (href && /^#fn-\d+$/.test(href)) {
    return (
      <sup>
        <a href={href} className="text-blue-600 hover:underline">
          {children}
        </a>
      </sup>
    );
  }
  if (href && /^https?:\/\//i.test(href)) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
        {children}
      </a>
    );
  }
  // http(s) 以外のリンクはリンクとして描画しない（design.md §7）。
  return <span>{children}</span>;
}

// @tailwindcss/typography は §13 の承認済み依存に無いため追加せず、見出し・箇条書きだけ
// 素の Tailwind ユーティリティで最低限読みやすくする。
const MARKDOWN_COMPONENTS = {
  a: SafeLink,
  h1: (props: HTMLAttributes<HTMLHeadingElement>) => (
    <h1 className="mb-2 mt-4 text-lg font-semibold first:mt-0" {...props} />
  ),
  h2: (props: HTMLAttributes<HTMLHeadingElement>) => (
    <h2 className="mb-2 mt-4 text-base font-semibold first:mt-0" {...props} />
  ),
  h3: (props: HTMLAttributes<HTMLHeadingElement>) => (
    <h3 className="mb-1 mt-3 text-sm font-semibold" {...props} />
  ),
  p: (props: HTMLAttributes<HTMLParagraphElement>) => <p className="mb-2 text-sm" {...props} />,
  ul: (props: HTMLAttributes<HTMLUListElement>) => (
    <ul className="mb-2 list-disc pl-5 text-sm" {...props} />
  ),
  ol: (props: OlHTMLAttributes<HTMLOListElement>) => (
    <ol className="mb-2 list-decimal pl-5 text-sm" {...props} />
  ),
  li: (props: LiHTMLAttributes<HTMLLIElement>) => <li className="mb-1" {...props} />,
  strong: (props: HTMLAttributes<HTMLElement>) => <strong className="font-semibold" {...props} />,
};

function EvidencePanel({ label, items }: { label: string; items: NodeEvidenceEntry[] }) {
  return (
    <div className="rounded border bg-white p-4">
      <h3 className="mb-2 text-sm font-semibold">根拠：{label}</h3>
      {items.length === 0 ? (
        <p className="text-sm text-gray-500">根拠が見つかりませんでした。</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((item, i) => (
            <li key={`${item.label}-${i}`} className="text-sm">
              <p className="mb-1 text-xs text-gray-500">{item.label}</p>
              {/* 原文は Markdown として解釈せず、テキストノードのまま一字一句表示する（I2）。 */}
              <p className="whitespace-pre-wrap break-words rounded bg-gray-50 p-2 font-mono text-xs">
                {item.text}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function ReportViewer({ report }: { report: ReportDetail }) {
  const [selected, setSelected] = useState<{ label: string; items: NodeEvidenceEntry[] } | null>(null);
  // 初期値の時点で mermaid_dsl の有無を反映しておく（ReportViewer は版ごとに key で再マウントするため、
  // report が変わった後にこの effect の中で同期的に setState し直す必要がない）。
  const [diagramState, setDiagramState] = useState<"loading" | "ok" | "failed">(() =>
    report.mermaid_dsl?.trim() ? "loading" : "failed"
  );
  const [svg, setSvg] = useState<string>("");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // withheld のときや、worker が mermaid_dsl を null で返したときは描画しない
    // （reports.mermaid_dsl は DB 上 nullable）。
    const dsl = report.mermaid_dsl;
    if (!dsl?.trim()) return;
    let cancelled = false;

    const timeoutId = setTimeout(() => {
      if (!cancelled) setDiagramState("failed");
    }, MERMAID_TIMEOUT_MS);

    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
        const id = `mermaid-v${report.version_no}-${Math.random().toString(36).slice(2, 8)}`;
        const result = await mermaid.render(id, dsl);
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
  }, [report.mermaid_dsl, report.version_no]);

  useEffect(() => {
    if (diagramState !== "ok" || !containerRef.current) return;
    const container = containerRef.current;
    const cleanups: (() => void)[] = [];

    for (const nodeId of Object.keys(report.node_evidence)) {
      const matches = container.querySelectorAll(`[id*="${nodeId}"]`);
      matches.forEach((el) => {
        const handler = () => setSelected({ label: nodeId, items: report.node_evidence[nodeId] });
        el.addEventListener("click", handler);
        el.addEventListener("pointerup", handler);
        (el as HTMLElement).style.cursor = "pointer";
        cleanups.push(() => {
          el.removeEventListener("click", handler);
          el.removeEventListener("pointerup", handler);
        });
      });
    }
    return () => cleanups.forEach((c) => c());
  }, [diagramState, report.node_evidence]);

  if (report.withheld) {
    return (
      <div className="rounded border bg-amber-50 p-6 text-sm text-amber-900">
        この版には非公開になった情報が含まれるため表示できません。次の実行で作り直されます。
      </div>
    );
  }

  // withheld のとき worker は flags を {} で返す（§5.6 は未規定）。ここでは
  // withheld=false の場合しか通らないが、型は Partial のため既定値で補う。
  const suspectedInjection = report.flags.suspected_injection ?? [];
  const unverifiedAiCount = report.flags.unverified_ai_count ?? 0;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3 text-sm text-gray-500">
        <span>生成日時：{formatJst(report.generated_at)}</span>
        <span>判定：{report.judge_status === "pass" ? "合格" : "要確認"}</span>
        <span>未確認の件数：{unverifiedAiCount}</span>
      </div>

      {suspectedInjection.length > 0 && (
        <div className="rounded bg-red-50 p-3 text-sm text-red-800">
          <p className="font-medium">誘導の疑いのある記述を検出</p>
          <p className="mt-1 text-xs">対象：{suspectedInjection.join("、")}</p>
        </div>
      )}

      {report.excluded_summary && (
        <div className="rounded bg-amber-50 p-3 text-sm text-amber-800">
          <p className="font-medium">除外されたソース：{report.excluded_summary.count}件</p>
          <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(REASON_LABEL).map(([reason, label]) => (
              <li key={reason}>
                {label}：{report.excluded_summary!.by_reason[reason as ExcludeReason] ?? 0}件
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <div className="flex flex-col gap-6">
          <article className="rounded border bg-white p-4">
            <ReactMarkdown components={MARKDOWN_COMPONENTS}>{linkFootnoteRefs(report.body_markdown)}</ReactMarkdown>
          </article>

          <section className="rounded border bg-white p-4">
            <h2 className="mb-3 text-sm font-semibold">図</h2>
            {diagramState === "loading" && <p className="text-sm text-gray-500">図を描画しています…</p>}
            {diagramState === "ok" && (
              // mermaid.render の出力（securityLevel: "strict"）を描画する。
              <div ref={containerRef} dangerouslySetInnerHTML={{ __html: svg }} />
            )}
            {diagramState === "failed" && (
              <div>
                <p className="mb-3 text-sm text-gray-600">
                  図を表示できなかったため、一覧で表示しています。
                </p>
                {report.timeline.length === 0 ? (
                  <p className="text-sm text-gray-500">表示できる項目がありません。</p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {[...report.timeline]
                      .sort((a, b) => compareIso(a.occurred_at, b.occurred_at))
                      .map((item) => {
                        const nodeId = `E${item.event_no}`;
                        return (
                          <li key={item.event_no}>
                            <button
                              type="button"
                              onClick={() =>
                                setSelected({ label: nodeId, items: report.node_evidence[nodeId] ?? [] })
                              }
                              className="w-full rounded border px-3 py-2 text-left text-sm hover:bg-gray-50"
                            >
                              <span className="text-gray-500">{formatJst(item.occurred_at)}</span>
                              {" ・ "}
                              <span className="text-gray-500">{KIND_LABEL[item.kind] ?? item.kind}</span>
                              {" ・ "}
                              <span>{item.summary}</span>
                            </button>
                          </li>
                        );
                      })}
                  </ul>
                )}
              </div>
            )}
          </section>

          <section className="rounded border bg-white p-4">
            <h2 className="mb-3 text-sm font-semibold">脚注</h2>
            {Object.keys(report.footnotes).length === 0 ? (
              <p className="text-sm text-gray-500">脚注はありません。</p>
            ) : (
              <ol className="flex flex-col gap-3">
                {Object.entries(report.footnotes)
                  .sort(([a], [b]) => Number(a) - Number(b))
                  .map(([n, entries]) => (
                    <li key={n} id={`fn-${n}`} className="scroll-mt-4 text-sm">
                      <p className="mb-1 text-xs text-gray-500">［{n}］</p>
                      {entries.map((entry, i) => (
                        <div key={`${entry.label}-${i}`} className="mb-1 rounded bg-gray-50 p-2">
                          <p className="text-xs text-gray-500">
                            {entry.label}（S{entry.source_no}・{entry.speaker ?? "不明"}・{formatJst(entry.recorded_at)}）
                          </p>
                          {/* 原文は Markdown として解釈せず、テキストノードのまま一字一句表示する（I2）。 */}
                          <p className="whitespace-pre-wrap break-words font-mono text-xs">{entry.text}</p>
                        </div>
                      ))}
                    </li>
                  ))}
              </ol>
            )}
          </section>
        </div>

        <div className="lg:sticky lg:top-4 lg:self-start">
          {selected ? (
            <EvidencePanel label={selected.label} items={selected.items} />
          ) : (
            <div className="rounded border bg-white p-4 text-sm text-gray-500">
              図のノードまたは一覧の項目を選ぶと、根拠の原文がここに表示されます。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
