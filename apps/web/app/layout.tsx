import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "旅拍助手 Agent",
  description: "TravelShot Agent phase 1"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
