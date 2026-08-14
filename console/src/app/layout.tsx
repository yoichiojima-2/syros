import type { Metadata } from "next";
import { ThemeProvider } from "next-themes";
import { AppShell } from "@/components/app-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "syros console",
  description: "Sessions, transcripts, and tool-call approvals for syros agents.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased">
        {/* re-apply the saved palette before first paint, like next-themes
            does for light/dark — keep the key in sync with palette-picker */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              'try{var p=localStorage.getItem("syros-palette");if(p)document.documentElement.dataset.palette=p}catch(e){}',
          }}
        />
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <AppShell>{children}</AppShell>
        </ThemeProvider>
      </body>
    </html>
  );
}
