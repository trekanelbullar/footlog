/**
 * レポート本文（worker が組んだ Markdown）を、記事の体裁で並べ直すための読み取り。
 *
 * worker の本文は「## 見出し」と「- 文 〔出どころ〕[^n]」の決まった形で届く（render.py）。
 * ここでは文字列を組み替えるだけで、文の中身は変えない（引用の原文は脚注の欄から出す、I2）。
 */

import type {
  NodeEvidenceEntry,
  ReportDetail,
  TimelineItem,
} from "@/lib/worker-types";

export type OriginTag = "human" | "ai_verified" | "ai_unverified" | "document";

export interface ArticleItem {
  text: string;
  footnotes: number[];
  origin: OriginTag | null;
  unresolvedReason: boolean;
  similarRejected: string[]; // 〔過去に却下した案と類似：E12〕の E12
}

export interface ArticleSection {
  heading: string;
  label: string; // 見出しの上の小さな英字
  kind: "decision" | "reason" | "rejected" | "open" | "status" | "other";
  items: ArticleItem[];
}

const ORIGIN_MARKERS: Record<string, OriginTag> = {
  "〔人が発案〕": "human",
  "〔AIの提案を人が確認〕": "ai_verified",
  "〔AIの提案のまま（未確認）〕": "ai_unverified",
  "〔資料に根拠〕": "document",
};

export const ORIGIN_LABEL: Record<OriginTag, string> = {
  human: "人が発案",
  ai_verified: "AIの提案を人が確認",
  ai_unverified: "AIの提案のまま（未確認）",
  document: "資料に根拠",
};

const UNRESOLVED_MARKER = "（理由は未確認：担当者に確認中）";
const SIMILAR_REJECTED = /〔過去に却下した案と類似：([^〕]*)〕/g;
const FOOTNOTE = /\[\^(\d+)\]/g;

function classify(heading: string): Pick<ArticleSection, "label" | "kind"> {
  if (heading.includes("決定")) return { label: "DECISION", kind: "decision" };
  if (heading.includes("理由")) return { label: "REASON", kind: "reason" };
  if (heading.includes("採用しなかった") || heading.includes("却下"))
    return { label: "REJECTED", kind: "rejected" };
  if (heading.includes("未解決") || heading.includes("次に"))
    return { label: "OPEN", kind: "open" };
  if (heading.includes("現在地")) return { label: "STATUS", kind: "status" };
  if (heading.includes("目的")) return { label: "PURPOSE", kind: "other" };
  return { label: "NOTE", kind: "other" };
}

function parseItem(raw: string): ArticleItem {
  let text = raw;
  const footnotes = [...text.matchAll(FOOTNOTE)].map((m) => Number(m[1]));
  text = text.replace(FOOTNOTE, "");

  const similarRejected: string[] = [];
  text = text.replace(SIMILAR_REJECTED, (_m, inner: string) => {
    similarRejected.push(...(inner.match(/E\d+/g) ?? []));
    return "";
  });

  let origin: OriginTag | null = null;
  for (const [marker, tag] of Object.entries(ORIGIN_MARKERS)) {
    if (text.includes(marker)) {
      origin = tag;
      text = text.replace(marker, "");
    }
  }

  const unresolvedReason = text.includes(UNRESOLVED_MARKER);
  text = text.replace(UNRESOLVED_MARKER, "");

  return {
    text: text.replace(/\s+/g, " ").trim(),
    footnotes,
    origin,
    unresolvedReason,
    similarRejected,
  };
}

/** 本文を見出しごとの節に分ける。見出しより前の行（件数の注記など）は捨てる。 */
export function parseArticle(markdown: string): ArticleSection[] {
  const sections: ArticleSection[] = [];
  let current: ArticleSection | null = null;
  for (const line of markdown.split("\n")) {
    const trimmed = line.trim();
    const heading = trimmed.match(/^#{1,3}\s+(.+)$/);
    if (heading) {
      current = {
        heading: heading[1].trim(),
        ...classify(heading[1]),
        items: [],
      };
      sections.push(current);
      continue;
    }
    if (!current || !trimmed || trimmed === "（該当なし）") continue;
    const body = trimmed.replace(/^[-*]\s+/, "");
    if (body === "（該当なし）") continue;
    current.items.push(parseItem(body));
  }
  return sections;
}

/** 脚注の原文の区切り番号（S3-12 など）を、図のノード（E12）の根拠と突き合わせるための表。 */
export function footnoteLabels(report: ReportDetail): Map<number, Set<string>> {
  const map = new Map<number, Set<string>>();
  for (const [n, entries] of Object.entries(report.footnotes)) {
    map.set(Number(n), new Set(entries.map((e) => e.label)));
  }
  return map;
}

/** 図のノード E12 の根拠と同じ原文を引いている脚注の番号（最初の1つ）。無ければ null。 */
export function footnoteForNode(
  report: ReportDetail,
  nodeId: string,
): number | null {
  const evidence: NodeEvidenceEntry[] = report.node_evidence[nodeId] ?? [];
  const labels = new Set(evidence.map((e) => e.label));
  const candidates = [...footnoteLabels(report).entries()]
    .filter(([, set]) => [...set].some((l) => labels.has(l)))
    .map(([n]) => n)
    .sort((a, b) => a - b);
  return candidates[0] ?? null;
}

/** この版で新しく増えた出来事（1つ前の版の時系列に無いもの）の番号。 */
export function newEventNos(
  current: TimelineItem[],
  previous: TimelineItem[] | null,
): Set<number> {
  if (!previous) return new Set();
  const before = new Set(previous.map((t) => t.event_no));
  return new Set(
    current.filter((t) => !before.has(t.event_no)).map((t) => t.event_no),
  );
}

/** 脚注の原文と、新しい出来事の根拠が重なるか（本文の項目に NEW を付けるため）。 */
export function isNewItem(
  item: ArticleItem,
  report: ReportDetail,
  fresh: Set<number>,
): boolean {
  if (fresh.size === 0 || item.footnotes.length === 0) return false;
  const labels = footnoteLabels(report);
  const itemLabels = new Set(
    item.footnotes.flatMap((n) => [...(labels.get(n) ?? [])]),
  );
  for (const no of fresh) {
    if (
      (report.node_evidence[`E${no}`] ?? []).some((e) =>
        itemLabels.has(e.label),
      )
    )
      return true;
  }
  return false;
}

/**
 * ヒーローの見出し：この版の決定のうち最も重要なものの summary をそのまま使う（LLM で作らない）。
 * 「最も重要」は、この版で新しく増えた決定のうち最も新しいもの。無ければ時系列で最も新しい決定。
 * 決定が1つも無ければ null。
 */
export function pickHeadline(
  timeline: TimelineItem[],
  fresh: Set<number>,
): TimelineItem | null {
  const decisions = timeline
    .filter((t) => t.kind === "decision")
    .sort(
      (a, b) =>
        new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime(),
    );
  return decisions.find((t) => fresh.has(t.event_no)) ?? decisions[0] ?? null;
}
