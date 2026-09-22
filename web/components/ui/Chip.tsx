import type { ReactNode } from "react";

// メタデータのチップ。色は意味ごとに固定（design/ui-guidelines.md g、ui-spec-3col.md）。
export type ChipTone = "gray" | "orange" | "green" | "amber" | "red" | "indigo";

const TONE: Record<ChipTone, string> = {
  gray: "border-transparent bg-[#e8e4dc] text-[#4a453d]",
  orange: "border-transparent bg-[#fbe3d3] text-[#b4541a]",
  green: "border-transparent bg-[#dff0dc] text-[#3d7a3a]",
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
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-semibold ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}
