import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string | null;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="text-[11px] font-medium tracking-[0.08em] text-muted-foreground uppercase">
          {label}
        </div>
      </CardHeader>
      <CardContent className="pt-2">
        {value === null ? (
          <Skeleton className="h-9 w-24" />
        ) : (
          <div className={cn("font-serif text-[30px] leading-none", accent && "text-primary")}>
            {value}
          </div>
        )}
        {sub && <div className="pt-2 text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}
