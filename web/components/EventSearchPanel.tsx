"use client";

import { useEffect, useState } from "react";
import { formatJst } from "@/lib/format";
import type { EventSearchResult } from "@/lib/worker-types";

// AD-12：出来事の検索（W25）。レポートの画面に置く検索窓。

const KIND_LABEL: Record<string, string> = {
  decision: "決定",
  rejected_option: "却下案",
  open_issue: "未解決",
  finding: "わかったこと",
  status: "現在地",
};

const MIN_QUERY_LENGTH = 2;
const DEBOUNCE_MS = 300;

export default function EventSearchPanel({ pid }: { pid: string }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"loading" | "ok" | "error">("ok");
  const [results, setResults] = useState<EventSearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<EventSearchResult | null>(null);

  const trimmedQuery = query.trim();
  // AD-12：2文字未満では送らない（表示側の判定なので effect の中で setState しない）。
  const tooShort = trimmedQuery.length < MIN_QUERY_LENGTH;

  useEffect(() => {
    if (tooShort) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setStatus("loading");
      setError(null);
      (async () => {
        try {
          const res = await fetch(
            `/api/projects/${pid}/events/search?q=${encodeURIComponent(trimmedQuery)}`,
            { signal: controller.signal }
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
      })();
    }, DEBOUNCE_MS);

    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [pid, trimmedQuery, tooShort]);

  return (
    <section className="rounded border bg-white p-4">
      <h2 className="mb-3 text-sm font-semibold">出来事を検索</h2>
      <input
        type="search"
        className="mb-3 w-full rounded border px-3 py-2 text-sm"
        placeholder="要約・理由の一部を入力（2文字以上）"
        value={query}
        onChange={(e) => {
          setSelected(null);
          setQuery(e.target.value);
        }}
      />

      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <div>
          {!tooShort && status === "loading" && (
            <p className="text-sm text-gray-500">検索しています…</p>
          )}
          {!tooShort && status === "error" && (
            <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>
          )}
          {!tooShort && status === "ok" && results.length === 0 && (
            <p className="text-sm text-gray-500">見つかりませんでした。</p>
          )}
          {!tooShort && status === "ok" && results.length > 0 && (
            <ul className="flex flex-col gap-2">
              {results.map((r) => (
                <li key={r.event_no}>
                  <button
                    type="button"
                    onClick={() => setSelected(r)}
                    className="w-full rounded border px-3 py-2 text-left text-sm hover:bg-gray-50"
                  >
                    <span className="text-gray-500">{KIND_LABEL[r.kind] ?? r.kind}</span>
                    {" ・ "}
                    <span className="text-gray-500">{formatJst(r.occurred_at)}</span>
                    {" ・ "}
                    <span>{r.summary}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          {selected ? (
            <div className="rounded border bg-gray-50 p-3">
              <p className="mb-2 text-xs text-gray-500">
                {KIND_LABEL[selected.kind] ?? selected.kind} ・ {formatJst(selected.occurred_at)}
              </p>
              <p className="mb-2 text-sm">{selected.summary}</p>
              {selected.evidence.length === 0 ? (
                <p className="text-sm text-gray-500">根拠が見つかりませんでした。</p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {selected.evidence.map((entry, i) => (
                    <li key={`${entry.label}-${i}`} className="text-sm">
                      <p className="mb-1 text-xs text-gray-500">{entry.label}</p>
                      {/* 原文は Markdown として解釈せず、テキストノードのまま一字一句表示する（I2）。 */}
                      <p className="whitespace-pre-wrap break-words rounded bg-white p-2 font-mono text-xs">
                        {entry.text}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500">結果を選ぶと根拠の原文がここに表示されます。</p>
          )}
        </div>
      </div>
    </section>
  );
}
