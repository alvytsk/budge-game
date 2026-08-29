import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { type RenderResult, render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { routeTree } from "../src/app/routes/routeTree.gen";

// Retries off and no cache between tests: a retrying query turns an
// assertion about one request into a five-second timeout.
function client(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
}

/** Pages render `<Link>`, which reads the router out of React context and
 * throws without one. `RouterContextProvider` supplies exactly that and
 * renders its children as given — unlike `RouterProvider`, which would
 * discard the component under test and render the matched route instead. */
function routerFor(): ReturnType<typeof createRouter> {
  return createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ["/host"] }) });
}

export function renderWithQuery(ui: ReactElement): RenderResult {
  const query = client();
  return render(
    <QueryClientProvider client={query}>
      <RouterContextProvider router={routerFor()}>{ui}</RouterContextProvider>
    </QueryClientProvider>,
  );
}

/** `renderHook` needs a wrapper component rather than a render call. */
export function withQuery() {
  const query = client();
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={query}>{children}</QueryClientProvider>;
  };
}
