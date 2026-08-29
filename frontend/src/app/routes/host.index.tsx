import { createFileRoute } from "@tanstack/react-router";
import { HomePage } from "@/pages/host-home";

export const Route = createFileRoute("/host/")({ component: HomePage });
