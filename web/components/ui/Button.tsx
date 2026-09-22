import Link from "next/link";
import type { ButtonHTMLAttributes, ReactNode } from "react";

// 押せるものは必ずこの部品を使う（design/ui-guidelines.md b）。
// primary は各画面に1つだけ（右上）。secondary はその隣。ghost は行の中の小さな操作だけ。
export type ButtonVariant = "primary" | "secondary" | "ghost";

const BASE =
  "inline-flex h-10 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-4 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gray-900 disabled:cursor-not-allowed disabled:opacity-50";

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-gray-900 text-white hover:bg-gray-700",
  secondary:
    "border border-gray-300 bg-white text-gray-900 hover:border-gray-400 hover:bg-gray-50",
  ghost: "h-8 px-2 text-gray-600 hover:bg-gray-100 hover:text-gray-900",
};

export function buttonClass(
  variant: ButtonVariant = "secondary",
  extra = "",
): string {
  return `${BASE} ${VARIANT[variant]} ${extra}`.trim();
}

export default function Button({
  variant = "secondary",
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      type={type}
      className={buttonClass(variant, className)}
      {...props}
    />
  );
}

/** 別の画面へ移動する操作をボタンの見た目で出すとき（空の状態の primary など）。 */
export function ButtonLink({
  href,
  variant = "secondary",
  children,
}: {
  href: string;
  variant?: ButtonVariant;
  children: ReactNode;
}) {
  return (
    <Link href={href} className={buttonClass(variant)}>
      {children}
    </Link>
  );
}
