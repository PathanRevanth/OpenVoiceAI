import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Voice-First AI",
  description:
    "Voice-first agentic control plane built on Pydantic AI v2: orchestrator, specialist agents, MCP, skills, approvals, and governance.",
};

/** Applies the persisted theme before first paint to avoid a flash. */
const themeBootstrap = `try{var t=localStorage.getItem("vf-theme");if(t==="dark"||t==="light"){document.documentElement.dataset.theme=t;}}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      data-theme="light"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
