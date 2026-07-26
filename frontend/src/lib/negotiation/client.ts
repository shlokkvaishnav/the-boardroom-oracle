import { LiveNegotiationClient } from "./live";
import { MockNegotiationClient } from "./mock";
import type { NegotiationClient } from "./types";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL as string | undefined;

export const IS_MOCK = !BACKEND_URL;

let singleton: NegotiationClient | null = null;

export function getNegotiationClient(): NegotiationClient {
  if (!singleton) {
    singleton = BACKEND_URL ? new LiveNegotiationClient(BACKEND_URL) : new MockNegotiationClient();
  }
  return singleton;
}
