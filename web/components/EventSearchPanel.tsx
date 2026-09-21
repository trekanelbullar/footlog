"use client";

import { useEffect, useState } from "react";
import { formatJst } from "@/lib/format";
import { TextField } from "@/components/ui/Field";
import { InlineError } from "@/components/ui/Feedback";
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
            { signal: controller.signal },
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
    <div className="w-full sm:w-80">
      <TextField
        label="出来事を検索"
        hideLabel
        type="search"
        placeholder="出来事を検索（2文字以上）"
        value={query}
        onChange={(e) => {
          setSelected(null);
          setQuery(e.target.value);
        }}
      />
      {!tooShort && (
        <div className="relative">
          <div className="absolute top-1 right-0 z-30 w-full max-w-[640px] rounded-md border border-gray-200 bg-white sm:w-[32rem]">
            {status === "loading" && (
              <p className="px-3 py-3 text-sm text-gray-500">検索しています…</p>
            )}
            {status === "error" && (
              <div className="p-2">
                <InlineError>{error}</InlineError>
              </div>
            )}
            {status === "ok" && results.length === 0 && (
              <p className="px-3 py-3 text-sm text-gray-500">
                見つかりませんでした。
              </p>
            )}
            {status === "ok" && results.length > 0 && (
              <ul className="max-h-80 divide-y divide-gray-100 overflow-y-auto">
                {results.map((r) => (
                  <li key={r.event_no}>
                    <button
                      type="button"
                      onClick={() =>
                        setSelected(
                          selected?.event_no === r.event_no ? null : r,
                        )
                      }
                      className="w-full px-3 py-2 text-left text-sm hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none"
                    >
                      <span className="text-xs text-gray-500">
                        {KIND_LABEL[r.kind] ?? r.kind} ・{" "}
                        {formatJst(r.occurred_at)}
                      </span>
                      <span className="block text-gray-900">{r.summary}</span>
                    </button>
                    {selected?.event_no === r.event_no && (
                      <div className="bg-gray-50 px-3 pb-3">
                        {selected.evidence.length === 0 ? (
                          <p className="text-sm text-gray-500">
                            根拠が見つかりませんでした。
                          </p>
                        ) : (
                          selected.evidence.map((entry, i) => (
                            <div
                              key={`${entry.label}-${i}`}
                              className="pt-2 text-sm"
                            >
                              <p className="text-xs text-gray-500">
                                {entry.label}
                              </p>
                              {/* 原文は Markdown として解釈せず、テキストノードのまま一字一句表示する（I2）。 */}
                              <p className="whitespace-pre-wrap break-words text-gray-900">
                                {entry.text}
                              </p>
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
