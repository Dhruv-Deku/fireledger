"use client";

import dynamic from "next/dynamic";

// The workspace needs the browser (WebGL map, saved setup in localStorage), so
// it renders only on the client. That also lets it read the saved setup as its
// initial state without a server/client mismatch.
const Workspace = dynamic(() => import("./Workspace"), {
  ssr: false,
  loading: () => (
    <main className="grid flex-1 place-items-center text-sm text-fl-muted">Loading</main>
  ),
});

export default function ClientWorkspace() {
  return <Workspace />;
}
