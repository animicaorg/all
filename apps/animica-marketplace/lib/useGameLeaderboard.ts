'use client';
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import {
  getLeaderboard,
  getPlayerKey,
  parseGameMessage,
  postScore,
  recordPlay,
  type Leaderboard,
} from './gameClient';

// Shared wiring for the play shell's score capture + leaderboard, used by BOTH the standalone
// /play/[slug] page and the in-page GamePlay panel so the (untrusted, cosmetic) leaderboard logic
// lives in one place.
//
// Trust model: the game iframe is opaque (sandbox="allow-scripts") so postMessage arrives with
// event.origin === "null" — we never trust origin. We trust that (a) the message came from OUR
// iframe's contentWindow (event.source identity) and (b) it matches the strict anm-game schema
// (parseGameMessage). The channel is one-way, read-only: we only READ score reports out, we never
// message anything back into the frame.

// Minimum spacing between score POSTs — coalesces a burst of terminal-score messages into one
// submit. The server also rate-limits; this just avoids obvious client-side spam.
const MIN_POST_MS = 1500;

export interface GameLeaderboardState {
  board: Leaderboard | null;
  /** Whether the leaderboard surface should be shown open (auto-opens on game-over/win). */
  open: boolean;
  setOpen: (v: boolean) => void;
  /** True once at least one terminal score has been reported this session. */
  reported: boolean;
  refresh: () => void;
}

export function useGameLeaderboard(
  slug: string,
  frameRef: RefObject<HTMLIFrameElement>,
): GameLeaderboardState {
  const [board, setBoard] = useState<Leaderboard | null>(null);
  const [open, setOpen] = useState(false);
  const [reported, setReported] = useState(false);

  // once-per-session play guard + last-score-post timestamp (refs so they don't trigger renders)
  const playedRef = useRef(false);
  const lastPostRef = useRef(0);
  const mountedRef = useRef(true);

  const refresh = useCallback(() => {
    getLeaderboard(slug, getPlayerKey())
      .then((b) => {
        if (mountedRef.current && b) setBoard(b);
      })
      .catch(() => {});
  }, [slug]);

  // First-record-of-the-session -> count a play. Fires on the first start OR score message so a
  // game that skips the optional "start" event still registers a play. Once per session.
  const ensurePlay = useCallback(() => {
    if (playedRef.current) return;
    playedRef.current = true;
    recordPlay(slug, getPlayerKey())
      .then(() => {
        if (mountedRef.current) refresh();
      })
      .catch(() => {});
  }, [slug, refresh]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Initial leaderboard load (public data — independent of whether the game has loaded).
  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const frame = frameRef.current;
      // Trust anchor: must originate from OUR iframe's window. Opaque origin means we can't (and
      // don't) use event.origin; source identity is the reliable signal that it's our game.
      if (!frame || !frame.contentWindow || e.source !== frame.contentWindow) return;

      const msg = parseGameMessage(e.data);
      if (!msg) return; // strict schema — ignore everything else

      ensurePlay();

      if (msg.event === 'score') {
        setReported(true);
        setOpen(true); // reveal the leaderboard on game-over / win

        const now = Date.now();
        if (now - lastPostRef.current < MIN_POST_MS) return; // drop rapid duplicate reports
        lastPostRef.current = now;

        postScore(slug, { score: msg.score, state: msg.state, playerKey: getPlayerKey() })
          .then((res) => {
            if (!mountedRef.current) return;
            // Re-read the board so "your best/rank" and the top list reflect the new score.
            if (res) refresh();
          })
          .catch(() => {});
      }
    }

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [slug, frameRef, ensurePlay, refresh]);

  return { board, open, setOpen, reported, refresh };
}
