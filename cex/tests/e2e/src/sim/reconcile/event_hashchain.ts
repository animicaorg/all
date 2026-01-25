/**
 * Event Hashchain
 * 
 * Builds cryptographic hashchain from ledger events to verify
 * integrity and detect tampering.
 */

import * as crypto from 'crypto';

export interface Event {
  id: string;
  type: string;
  timestamp: string;
  data: any;
}

export interface HashedEvent {
  event: Event;
  hash: string;
  previousHash: string;
  index: number;
}

export interface Hashchain {
  events: HashedEvent[];
  headHash: string;
  length: number;
  startTime: string;
  endTime: string;
}

/**
 * Build hashchain from events
 */
export function buildHashchain(events: Event[]): Hashchain {
  console.log(`[Hashchain] Building chain from ${events.length} events...`);
  
  const sortedEvents = [...events].sort((a, b) => 
    new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );
  
  const hashedEvents: HashedEvent[] = [];
  let previousHash = '0'.repeat(64); // Genesis hash
  
  for (let i = 0; i < sortedEvents.length; i++) {
    const event = sortedEvents[i];
    const hash = hashEvent(event, previousHash, i);
    
    hashedEvents.push({
      event,
      hash,
      previousHash,
      index: i,
    });
    
    previousHash = hash;
  }
  
  const hashchain: Hashchain = {
    events: hashedEvents,
    headHash: previousHash,
    length: hashedEvents.length,
    startTime: sortedEvents[0]?.timestamp || '',
    endTime: sortedEvents[sortedEvents.length - 1]?.timestamp || '',
  };
  
  console.log(`[Hashchain] Chain complete`);
  console.log(`[Hashchain] Length: ${hashchain.length}`);
  console.log(`[Hashchain] Head: ${hashchain.headHash}`);
  
  return hashchain;
}

/**
 * Verify hashchain integrity
 */
export function verifyHashchain(hashchain: Hashchain): {
  valid: boolean;
  errors: string[];
} {
  console.log(`[Hashchain] Verifying chain of ${hashchain.length} events...`);
  
  const errors: string[] = [];
  let previousHash = '0'.repeat(64);
  
  for (let i = 0; i < hashchain.events.length; i++) {
    const hashedEvent = hashchain.events[i];
    
    // Check index
    if (hashedEvent.index !== i) {
      errors.push(`Event ${i}: index mismatch (expected ${i}, got ${hashedEvent.index})`);
    }
    
    // Check previous hash
    if (hashedEvent.previousHash !== previousHash) {
      errors.push(`Event ${i}: previous hash mismatch`);
    }
    
    // Verify hash
    const expectedHash = hashEvent(hashedEvent.event, previousHash, i);
    if (hashedEvent.hash !== expectedHash) {
      errors.push(`Event ${i}: hash mismatch (expected ${expectedHash}, got ${hashedEvent.hash})`);
    }
    
    previousHash = hashedEvent.hash;
  }
  
  // Check head hash
  if (previousHash !== hashchain.headHash) {
    errors.push(`Head hash mismatch (expected ${previousHash}, got ${hashchain.headHash})`);
  }
  
  const valid = errors.length === 0;
  
  if (valid) {
    console.log(`[Hashchain] ✓ Chain is valid`);
  } else {
    console.log(`[Hashchain] ✗ Chain is invalid: ${errors.length} errors`);
    errors.forEach(err => console.log(`  - ${err}`));
  }
  
  return { valid, errors };
}

/**
 * Find event in hashchain by ID
 */
export function findEvent(hashchain: Hashchain, eventId: string): HashedEvent | undefined {
  return hashchain.events.find(e => e.event.id === eventId);
}

/**
 * Get events by type
 */
export function getEventsByType(hashchain: Hashchain, type: string): HashedEvent[] {
  return hashchain.events.filter(e => e.event.type === type);
}

/**
 * Get events in time range
 */
export function getEventsByTimeRange(
  hashchain: Hashchain,
  startTime: string,
  endTime: string
): HashedEvent[] {
  const start = new Date(startTime).getTime();
  const end = new Date(endTime).getTime();
  
  return hashchain.events.filter(e => {
    const time = new Date(e.event.timestamp).getTime();
    return time >= start && time <= end;
  });
}

/**
 * Compute hashchain merkle root
 */
export function computeMerkleRoot(hashchain: Hashchain): string {
  if (hashchain.events.length === 0) {
    return '0'.repeat(64);
  }
  
  let hashes = hashchain.events.map(e => e.hash);
  
  // Build merkle tree
  while (hashes.length > 1) {
    const nextLevel: string[] = [];
    
    for (let i = 0; i < hashes.length; i += 2) {
      const left = hashes[i];
      const right = hashes[i + 1] || left; // Duplicate if odd
      
      const combined = left + right;
      const hash = crypto.createHash('sha256').update(combined).digest('hex');
      nextLevel.push(hash);
    }
    
    hashes = nextLevel;
  }
  
  return hashes[0];
}

/**
 * Hash a single event
 */
function hashEvent(event: Event, previousHash: string, index: number): string {
  const data = JSON.stringify({
    index,
    previousHash,
    id: event.id,
    type: event.type,
    timestamp: event.timestamp,
    data: event.data,
  });
  
  return crypto.createHash('sha256').update(data).digest('hex');
}

/**
 * Export hashchain to JSON
 */
export function exportHashchain(hashchain: Hashchain): string {
  return JSON.stringify(hashchain, null, 2);
}

/**
 * Load hashchain from JSON
 */
export function loadHashchain(json: string): Hashchain {
  return JSON.parse(json);
}

/**
 * Create proof of inclusion for an event
 */
export function createInclusionProof(
  hashchain: Hashchain,
  eventId: string
): {
  found: boolean;
  event?: HashedEvent;
  proofPath?: string[];
  merkleRoot?: string;
} {
  const event = findEvent(hashchain, eventId);
  
  if (!event) {
    return { found: false };
  }
  
  // Build merkle proof path (simplified)
  const merkleRoot = computeMerkleRoot(hashchain);
  const proofPath: string[] = [];
  
  // Add previous and next hashes as proof
  if (event.index > 0) {
    proofPath.push(hashchain.events[event.index - 1].hash);
  }
  
  if (event.index < hashchain.events.length - 1) {
    proofPath.push(hashchain.events[event.index + 1].hash);
  }
  
  return {
    found: true,
    event,
    proofPath,
    merkleRoot,
  };
}
