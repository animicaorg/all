/**
 * Anti-Phishing Utilities
 * Helps users identify legitimate communications from the platform
 */

import { randomBytes } from 'crypto';

/**
 * Generate a random anti-phishing phrase
 * This phrase is shown to users on login and in emails
 */
export function generateAntiPhishingPhrase(): string {
  const adjectives = [
    'Happy',
    'Bright',
    'Swift',
    'Calm',
    'Bold',
    'Wise',
    'Cool',
    'Kind',
    'Noble',
    'Brave',
  ];

  const nouns = [
    'Tiger',
    'Eagle',
    'Dolphin',
    'Phoenix',
    'Dragon',
    'Lion',
    'Falcon',
    'Wolf',
    'Bear',
    'Shark',
  ];

  const randomIndex1 = randomBytes(1)[0] % adjectives.length;
  const randomIndex2 = randomBytes(1)[0] % nouns.length;
  const randomNumber = randomBytes(2).readUInt16BE(0) % 1000;

  return `${adjectives[randomIndex1]} ${nouns[randomIndex2]} ${randomNumber}`;
}

/**
 * Validate anti-phishing phrase format
 */
export function isValidAntiPhishingPhrase(phrase: string): boolean {
  // Basic validation: 2-3 words, reasonable length
  const words = phrase.trim().split(/\s+/);
  return words.length >= 2 && words.length <= 4 && phrase.length <= 50;
}

/**
 * Sanitize user-provided anti-phishing phrase
 */
export function sanitizeAntiPhishingPhrase(phrase: string): string {
  return phrase
    .trim()
    .replace(/[^a-zA-Z0-9\s]/g, '')
    .slice(0, 50);
}
