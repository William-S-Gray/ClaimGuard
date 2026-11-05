// Realtime client — connects to the backend WebSocket gateway and re-emits
// events to subscribers. Emitter API mirrors the former mock emitter
// (connect/disconnect/on/off) so existing listeners work unchanged.
import type { WSEvent, WSEventType } from '@/types';
import { getToken, refreshAccessToken } from '@/lib/apiClient';

type EventHandler = (event: WSEvent) => void;
type StatusHandler = (connected: boolean) => void;

function resolveWsUrl(token: string): string {
  const base = (() => {
    const explicit = import.meta.env.VITE_WS_URL as string | undefined;
    if (explicit) return explicit;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/api/v1/ws`;
  })();
  // The gateway carries PHI events — authenticate with the access token.
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}token=${encodeURIComponent(token)}`;
}

class RealtimeClient {
  private ws: WebSocket | null = null;
  private handlers = new Map<WSEventType | '*', EventHandler[]>();
  private statusHandlers: StatusHandler[] = [];
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  // Set when the server closed us for policy (e.g. the token expired): the held
  // token is no good, so mint a fresh one before reconnecting.
  private needsFreshToken = false;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private shouldReconnect = false;
  private connected = false;

  isConnected(): boolean {
    return this.connected;
  }

  connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.shouldReconnect = true;
    void this.open();
  }

  private async open(): Promise<void> {
    // The socket cannot send Authorization headers, so mint/refresh an access
    // token and pass it as a query param. Without one there is no session.
    let token = this.needsFreshToken ? null : getToken();
    this.needsFreshToken = false;
    if (!token) {
      const ok = await refreshAccessToken();
      token = ok ? getToken() : null;
    }
    if (!token) {
      this.scheduleReconnect();
      return;
    }

    let socket: WebSocket;
    try {
      socket = new WebSocket(resolveWsUrl(token));
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = socket;

    socket.onopen = () => {
      this.setConnected(true);
      this.pingTimer = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send('ping');
      }, 25_000);
    };

    socket.onmessage = (msg) => {
      let event: WSEvent;
      try {
        event = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (!event || typeof event.type !== 'string') return;
      this.dispatch(event);
    };

    socket.onclose = (ev) => {
      if (ev.code === 1008) this.needsFreshToken = true;
      this.cleanupSocket();
      this.setConnected(false);
      this.scheduleReconnect();
    };

    socket.onerror = () => {
      // onclose will follow and handle reconnect.
      socket.close();
    };
  }

  private cleanupSocket(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (!this.shouldReconnect || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.open();
    }, 3000);
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.cleanupSocket();
    this.ws?.close();
    this.ws = null;
    this.setConnected(false);
  }

  private setConnected(value: boolean): void {
    if (this.connected === value) return;
    this.connected = value;
    this.statusHandlers.forEach((h) => h(value));
  }

  private dispatch(event: WSEvent): void {
    const typed = this.handlers.get(event.type as WSEventType) ?? [];
    const wildcard = this.handlers.get('*') ?? [];
    [...typed, ...wildcard].forEach((h) => h(event));
  }

  on(type: WSEventType | '*', handler: EventHandler): void {
    const existing = this.handlers.get(type) ?? [];
    this.handlers.set(type, [...existing, handler]);
  }

  off(type: WSEventType | '*', handler: EventHandler): void {
    const existing = this.handlers.get(type) ?? [];
    this.handlers.set(type, existing.filter((h) => h !== handler));
  }

  onStatus(handler: StatusHandler): void {
    this.statusHandlers.push(handler);
  }

  offStatus(handler: StatusHandler): void {
    this.statusHandlers = this.statusHandlers.filter((h) => h !== handler);
  }
}

export const realtimeClient = new RealtimeClient();
