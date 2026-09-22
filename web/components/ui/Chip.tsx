import type { ReactNode } from "react";

// メタデータのチップ。色は意味ごとに固定（design/ui-guidelines.md g、ui-spec-3col.md）。
export type ChipTone = "gray" | "orange" | "green" | "amber" | "red" | "indigo";

const TONE: Record<ChipTone, string> = {
  gray: "border-gray-300 bg-gray-50 text-gray-700",
  orange: "border-orange-300 bg-orange-50 text-orange-800",
  green: "border-green-300 bg-green-50 text-green-800",
  amber: "border-amber-300 bg-amber-50 text-amber-800",
  red: "border-red-300 bg-red-50 text-red-700",
  indigo: "border-indigo-300 bg-indigo-50 text-indigo-800",
};

export default function Chip({
  tone = "gray",
  children,
}: {
  tone?: ChipTone;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}
