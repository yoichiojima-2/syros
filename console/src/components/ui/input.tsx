import * as React from "react";
import { cn } from "@/lib/utils";

function Input({ className, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "h-8 w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 text-[13px] transition-colors placeholder:text-faint focus-visible:border-ring focus-visible:outline-none disabled:opacity-40",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
