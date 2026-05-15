/** Reconnecting WebSocket client bridging backend domain events to UI. */

import { useAuthStore } from "@/stores/authStore";

export interface WsEvent {
  type: "event";
  name: string;
  source: string;
  payload: Record<string, unknown>;
  occurred_at: string;
  event_id: string;
}

type Handler = (event: WsEvent) => void;

export interface WebSocketClientOptions {
  url?: string;
  topics?: string[];
  reconnectDelayMs?: number;
}

export class WebSocketClient {
  private socket: WebSocket | null = null;
  private handlers: Set<Handler> = new Set();
  private url: string;
  private topics: string[];
  private reconnectDelay: number;
  private shouldReconnect = true;
  private reconnectTimer: number | null = null;

  constructor(opts: WebSocketClientOptions = {}) {
    this.topics = opts.topics ?? [];
    this.reconnectDelay = opts.reconnectDelayMs ?? 2000;
    this.url = opts.url ?? "";
  }

  private deriveUrl(): string {
    // Stage 14: include the current access token so the backend can
    // enforce auth on the upgrade. We pull it fresh on each connect
    // attempt so a token refresh (via apiClient.refreshTokens) propagates
    // automatically — old sockets close on 401-equivalent and the
    // reconnect loop picks up the new token.
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const params = new URLSearchParams();
    const tokens = useAuthStore.getState().tokens;
    if (tokens?.accessToken) params.set("token", tokens.accessToken);
    if (this.topics.length) params.set("topics", this.topics.join(","));
    const qs = params.toString();
    return `${proto}//${location.host}/api/v1/ws${qs ? `?${qs}` : ""}`;
  }

  connect(): void {
    this.shouldReconnect = true;
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    // Re-derive on every connect so refreshed tokens take effect.
    const url = this.url || this.deriveUrl();
    this.socket = new WebSocket(url);

    this.socket.addEventListener("message", (e) => {
      try {
        const data = JSON.parse(e.data) as WsEvent;
        if (data?.type === "event") {
          this.handlers.forEach((h) => h(data));
        }
      } catch {
        // ignore malformed frames
      }
    });

    this.socket.addEventListener("close", () => {
      this.socket = null;
      if (this.shouldReconnect) {
        this.reconnectTimer = window.setTimeout(() => this.connect(), this.reconnectDelay);
      }
    });
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.socket?.close();
    this.socket = null;
  }

  subscribe(handler: Handler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }
}
