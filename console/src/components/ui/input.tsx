import * as React from "react";
import { cn } from "@/lib/utils";

function Input({ className, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "h-9 w-full min-w-0 rounded-lg border border-input bg-card px-3 text-[13px] transition-colors placeholder:text-faint focus-visible:border-ring focus-visible:outline-none disabled:opacity-40",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
