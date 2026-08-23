import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  component: () => (
    <main className="flex h-full items-center justify-center font-display text-4xl">budge</main>
  ),
});
