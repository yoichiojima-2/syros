"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

// Native input, not a Radix primitive: the console only needs a box that can
// also render the tri-state "some rows selected" header.
function Checkbox({
  className,
  indeterminate = false,
  ...props
}: React.ComponentProps<"input"> & { indeterminate?: boolean }) {
  const ref = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <input
      ref={ref}
      type="checkbox"
      className={cn(
        "size-3.5 cursor-pointer accent-primary align-middle",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        "disabled:cursor-not-allowed disabled:opacity-30",
        className,
      )}
      {...props}
    />
  );
}

export { Checkbox };
