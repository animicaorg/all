/**
 * ReorgHandler - Handles reorg detection and user notification
 */

import { useEffect, useRef } from 'react';
import type { ChainHead } from '../../lib/rpc/schemas';
import { emitToast } from '../../App';

export interface ReorgHandlerProps {
  enabled?: boolean;
}

export function useReorgHandler(props: ReorgHandlerProps = {}) {
  const { enabled = true } = props;
  const lastNotificationRef = useRef<number>(0);
  const NOTIFICATION_THROTTLE_MS = 10000; // Don't spam notifications - max one per 10s

  const handleReorg = (oldHead: ChainHead, newHead: ChainHead) => {
    if (!enabled) return;

    // Throttle notifications
    const now = Date.now();
    if (now - lastNotificationRef.current < NOTIFICATION_THROTTLE_MS) {
      console.warn('[ReorgHandler] Reorg notification throttled');
      return;
    }
    lastNotificationRef.current = now;

    console.warn('[ReorgHandler] Chain reorg detected', {
      height: newHead.height,
      oldHash: oldHead.hash.substring(0, 10) + '...',
      newHash: newHead.hash.substring(0, 10) + '...',
    });

    emitToast({
      title: '🔀 Chain Reorg Detected',
      message: `Block ${newHead.height} changed. Data is being refreshed.`,
      kind: 'warning',
      durationMs: 8000,
    });
  };

  return { handleReorg };
}
