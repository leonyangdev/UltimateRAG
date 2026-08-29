import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** 合并条件类名，并按 Tailwind 的覆盖规则消除冲突 Utility。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
