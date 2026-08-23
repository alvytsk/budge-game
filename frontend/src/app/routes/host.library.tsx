import { createFileRoute } from "@tanstack/react-router";
import { LibraryPage } from "@/pages/host-library";

export const Route = createFileRoute("/host/library")({ component: LibraryPage });
