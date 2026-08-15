"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useConnectors, useNow } from "@/lib/hooks";
import { relTime } from "@/lib/format";

// A connector is an official, vendor-hosted remote MCP server plus one
// credential in Secret Manager (syros-connector-{name}). Sessions opt in with
// connectors=["slack", ...] on an agent, deployment, or run; every tool call
// still flows through the audit trail and the approval gate. Credentials are
// written from an operator's machine — `syros connectors auth <name>` (OAuth)
// or `set` (static tokens) — never through this console.

export default function ConnectorsPage() {
  const connectors = useConnectors();
  const now = useNow();

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div className="flex items-center gap-3">
        <h1 className="flex-1 font-serif text-2xl tracking-tight">Connectors</h1>
      </div>
      <Card>
        <CardContent className="px-2 py-2">
          {connectors === null ? (
            <div className="space-y-2 p-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Platform</TableHead>
                  <TableHead>Auth</TableHead>
                  <TableHead>Servers</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {connectors.map((connector) => (
                  <TableRow key={connector.name}>
                    <TableCell className="font-mono text-[12px]">{connector.name}</TableCell>
                    <TableCell>{connector.label}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {connector.auth === "token" ? "static token" : "oauth"}
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">
                      {connector.servers.join(", ")}
                    </TableCell>
                    <TableCell>
                      {connector.configured ? (
                        <Badge className="border-ok/50 text-ok">configured</Badge>
                      ) : (
                        <span className="font-mono text-[11px] text-muted-foreground">
                          syros connectors {connector.auth === "token" ? "set" : "auth"}{" "}
                          {connector.name}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right text-[12px] text-muted-foreground">
                      {connector.configured ? relTime(connector.updated, now) : ""}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <p className="text-[12px] text-muted-foreground">
        Connectors mount the platform&apos;s official hosted MCP server into a session; credentials
        live in Secret Manager and are injected inside the sandbox at run start. Tool calls stay
        audited and gateable like any other tool. Verify a stored credential with{" "}
        <span className="font-mono">syros connectors test</span>; see{" "}
        <span className="font-mono">docs/connectors.md</span> for the full setup guide.
      </p>
    </div>
  );
}
