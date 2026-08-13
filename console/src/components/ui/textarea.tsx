import * as React from "react";
import { cn } from "@/lib/utils";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        "w-full resize-none bg-transparent text-sm transition-colors placeholder:text-faint focus-visible:outline-none disabled:opacity-40",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
