import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

function Select({ className, children, ...props }: React.ComponentProps<"select">) {
  return (
    <span className={cn("relative block w-full", className)}>
      <select
        className="h-9 w-full min-w-0 appearance-none rounded-lg border border-input bg-card pr-8 pl-3 text-[13px] transition-colors focus-visible:border-ring focus-visible:outline-none disabled:opacity-40"
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
    </span>
  );
}

export { Select };
