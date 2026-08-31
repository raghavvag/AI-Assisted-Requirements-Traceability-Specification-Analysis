import { clsx } from "clsx"
import { twMerge } from "tailwind-merge"

/** shadcn/ui class merger: conditional classes + Tailwind conflict resolution. */
export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
