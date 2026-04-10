/**
 * Test setup file
 * Sets up the test environment and loads environment variables
 */

import { config } from 'dotenv';

// Load test environment variables
config({ path: '.env.test' });

// Set default test database URL if not provided
if (!process.env.DATABASE_URL) {
  process.env.DATABASE_URL = 'postgresql://test:test@localhost:5432/exchange_test';
}
