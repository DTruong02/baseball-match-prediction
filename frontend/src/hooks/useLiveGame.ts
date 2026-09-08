"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchGameEvents,
  fetchLiveSnapshot,
  liveGameWebSocketUrl,
} from "@/lib/api";
import { getStoredToken } from "@/lib/auth-storage";
import type {
  GameEvent,
  LiveConnectionStatus,
  LiveState,
  LiveWsMessage,
} from "@/lib/types";

const POLL_INTERVAL_MS = 5_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 15_000;
const MAX_WS_FAILURES_BEFORE_POLL = 2;

function isLiveState(value: unknown): value is LiveState {
  return (
    typeof value === "object" &&
    value !== null &&
    "game_pk" in value &&
    typeof (value as LiveState).game_pk === "number"
  );
}

export function useLiveGame(gamePk: number | null) {
  const [live, setLive] = useState<LiveState | null>(null);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [connectionStatus, setConnectionStatus] =
    useState<LiveConnectionStatus>("connecting");
  const [degraded, setDegraded] = useState(false);
  const [source, setSource] = useState<"redis" | "postgres" | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const failuresRef = useRef(0);
  const lastEventsInsertedRef = useRef(0);
  const intentionalCloseRef = useRef(false);
  const modeRef = useRef<"ws" | "poll">("ws");

  const refreshEvents = useCallback(async (pk: number) => {
    try {
      const next = await fetchGameEvents(pk);
      setEvents(next);
    } catch {
      // Keep prior timeline if a refresh fails.
    }
  }, []);

  const applySnapshot = useCallback(
    (
      next: LiveState | null,
      nextSource: "redis" | "postgres" | null,
      nextDegraded: boolean,
      pk: number,
    ) => {
      setLive(next);
      setSource(nextSource);
      setDegraded(nextDegraded);
      const inserted = next?.events_inserted ?? 0;
      if (inserted !== lastEventsInsertedRef.current) {
        lastEventsInsertedRef.current = inserted;
        void refreshEvents(pk);
      }
    },
    [refreshEvents],
  );

  const pollOnce = useCallback(
    async (pk: number) => {
      try {
        const [snapshot] = await Promise.all([
          fetchLiveSnapshot(pk),
          refreshEvents(pk),
        ]);
        applySnapshot(
          snapshot.data,
          snapshot.source,
          snapshot.degraded,
          pk,
        );
      } catch {
        setConnectionStatus("offline");
      }
    },
    [applySnapshot, refreshEvents],
  );

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current != null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (pk: number) => {
      modeRef.current = "poll";
      setConnectionStatus("polling");
      stopPolling();
      void pollOnce(pk);
      pollTimerRef.current = setInterval(() => {
        void pollOnce(pk);
      }, POLL_INTERVAL_MS);
    },
    [pollOnce, stopPolling],
  );

  const clearReconnect = useCallback(() => {
    if (reconnectTimerRef.current != null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (gamePk == null || !Number.isFinite(gamePk)) {
      return;
    }

    intentionalCloseRef.current = false;
    failuresRef.current = 0;
    modeRef.current = "ws";
    lastEventsInsertedRef.current = 0;

    const connect = () => {
      const token = getStoredToken();
      if (!token) {
        startPolling(gamePk);
        return;
      }

      clearReconnect();
      stopPolling();
      modeRef.current = "ws";
      setConnectionStatus(
        failuresRef.current > 0 ? "reconnecting" : "connecting",
      );

      const ws = new WebSocket(liveGameWebSocketUrl(gamePk, token));
      wsRef.current = ws;

      ws.onopen = () => {
        failuresRef.current = 0;
        setConnectionStatus("connected");
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(String(event.data)) as LiveWsMessage;
          if (
            (message.type === "snapshot" || message.type === "update") &&
            isLiveState(message.data)
          ) {
            applySnapshot(
              message.data,
              message.source,
              message.degraded,
              gamePk,
            );
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      ws.onerror = () => {
        // onclose handles recovery.
      };

      ws.onclose = () => {
        if (intentionalCloseRef.current) {
          return;
        }
        wsRef.current = null;
        failuresRef.current += 1;

        if (failuresRef.current >= MAX_WS_FAILURES_BEFORE_POLL) {
          startPolling(gamePk);
          return;
        }

        setConnectionStatus("reconnecting");
        const delay = Math.min(
          RECONNECT_BASE_MS * 2 ** (failuresRef.current - 1),
          RECONNECT_MAX_MS,
        );
        reconnectTimerRef.current = setTimeout(() => {
          if (!intentionalCloseRef.current && modeRef.current === "ws") {
            connect();
          }
        }, delay);
      };
    };

    // Defer subscription boot so status/event updates are not sync setState in the effect.
    const bootTimer = setTimeout(() => {
      void refreshEvents(gamePk);
      connect();
    }, 0);

    return () => {
      intentionalCloseRef.current = true;
      clearTimeout(bootTimer);
      clearReconnect();
      stopPolling();
      if (wsRef.current != null) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [
    applySnapshot,
    clearReconnect,
    gamePk,
    refreshEvents,
    startPolling,
    stopPolling,
  ]);

  return {
    live,
    events,
    connectionStatus,
    degraded,
    source,
  };
}
