import { useId } from "react";
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

// 入力欄は必ずこの部品を使う（design/ui-guidelines.md d）。
// ラベルは上に太字、説明はその下に薄く、エラーは欄の直下に赤字。幅は最大 640px。

const CONTROL =
  "w-full rounded-md border border-gray-300 bg-white px-3 text-sm text-gray-900 placeholder:text-gray-400 focus:border-gray-900 focus:outline-none focus:ring-1 focus:ring-gray-900 disabled:bg-gray-50 aria-[invalid=true]:border-red-600";

interface FieldFrameProps {
  label: string;
  help?: ReactNode;
  error?: string | null;
  required?: boolean;
  optional?: boolean;
  hideLabel?: boolean;
  className?: string;
}

function Frame({
  id,
  label,
  help,
  error,
  required,
  optional,
  hideLabel,
  className = "",
  children,
}: FieldFrameProps & { id: string; children: ReactNode }) {
  return (
    <div className={`flex w-full max-w-[640px] flex-col gap-1 ${className}`}>
      <label
        htmlFor={id}
        className={
          hideLabel ? "sr-only" : "text-sm font-semibold text-gray-900"
        }
      >
        {label}
        {required && (
          <span className="ml-1 text-xs font-normal text-red-600">必須</span>
        )}
        {optional && (
          <span className="ml-1 text-xs font-normal text-gray-500">任意</span>
        )}
      </label>
      {help && !hideLabel && <p className="text-xs text-gray-500">{help}</p>}
      {children}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}

export function TextField({
  label,
  help,
  error,
  optional,
  hideLabel,
  className,
  ...props
}: FieldFrameProps & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <Frame
      id={id}
      {...{ label, help, error, optional, hideLabel, className }}
      required={props.required}
    >
      <input
        id={id}
        aria-invalid={Boolean(error)}
        className={`${CONTROL} h-10`}
        {...props}
      />
    </Frame>
  );
}

export function TextAreaField({
  label,
  help,
  error,
  optional,
  hideLabel,
  className,
  ...props
}: FieldFrameProps & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const id = useId();
  return (
    <Frame
      id={id}
      {...{ label, help, error, optional, hideLabel, className }}
      required={props.required}
    >
      <textarea
        id={id}
        aria-invalid={Boolean(error)}
        className={`${CONTROL} min-h-[120px] py-2`}
        {...props}
      />
    </Frame>
  );
}

export function SelectField({
  label,
  help,
  error,
  optional,
  hideLabel,
  className,
  children,
  ...props
}: FieldFrameProps & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <Frame
      id={id}
      {...{ label, help, error, optional, hideLabel, className }}
      required={props.required}
    >
      <select
        id={id}
        aria-invalid={Boolean(error)}
        className={`${CONTROL} h-10`}
        {...props}
      >
        {children}
      </select>
    </Frame>
  );
}

export function FileField({
  label,
  help,
  error,
  optional,
  className,
  ...props
}: FieldFrameProps & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <Frame
      id={id}
      {...{ label, help, error, optional, className }}
      required={props.required}
    >
      <input
        id={id}
        type="file"
        className="block w-full rounded-md border border-gray-300 bg-white text-sm text-gray-700 file:mr-3 file:h-10 file:border-0 file:border-r file:border-gray-300 file:bg-gray-50 file:px-4 file:text-sm file:font-medium hover:file:bg-gray-100 focus:border-gray-900 focus:outline-none"
        {...props}
      />
    </Frame>
  );
}
