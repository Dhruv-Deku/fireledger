import type { Metadata } from "next";
import { Archivo } from "next/font/google";
import { ThemeProvider, THEME_INIT_SCRIPT } from "@/lib/theme";
import StudioIntro from "@/components/StudioIntro";
import { INTRO_INIT_SCRIPT } from "@/lib/intro";
import "./globals.css";

// one family, two voices: expanded width for headings, normal width for reading
const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
  axes: ["wdth"],
});

export const metadata: Metadata = {
  title: "FireLedger",
  description: "Satellite fire hotspots from MODIS and VIIRS on one scale, 2000 to today, for any area you draw.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${archivo.variable} h-full antialiased`}
    >
      <head>
        {/* Sets data-theme + the dark class before first paint (no flash). */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        {/* the studio intro plays once per visit; later loads hide it before paint */}
        <script dangerouslySetInnerHTML={{ __html: INTRO_INIT_SCRIPT }} />
      </head>
      <body className="h-full flex flex-col">
        <StudioIntro />
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
