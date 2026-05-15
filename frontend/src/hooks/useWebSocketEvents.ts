import { useEffect, useRef } from "react";

import { WebSocketClient, type WsEvent } from "@/services/wsClient";

let singleton: WebSocketClient | null = null;

function getClient(): WebSocketClient {
  if (!singleton) singleton = new WebSocketClient();
  return singleton;
}

/** Subscribe to events from the shared WS client. */
export function useWebSocketEvents(handler: (event: WsEvent) => void): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const client = getClient();
    client.connect();
    const unsub = client.subscribe((e) => ref.current(e));
    return unsub;
  }, []);
}
