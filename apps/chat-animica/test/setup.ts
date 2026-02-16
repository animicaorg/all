process.env.DATABASE_URL = process.env.DATABASE_URL ?? "postgresql://localhost:5432/dev";
process.env.REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";
process.env.JWT_SECRET = process.env.JWT_SECRET ?? "test-jwt-secret-123456";
process.env.WALLET_CONNECT_SIGNING_KEY = process.env.WALLET_CONNECT_SIGNING_KEY ?? "test-wallet-signing-key-123456";
