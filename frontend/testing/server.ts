import { setupServer } from "msw/node";

/** No default handlers: every test declares the responses it depends on,
 * so a test that forgot one fails loudly rather than passing against a
 * fixture written for a different test. */
export const server = setupServer();
