import express from "express";
import { z } from "zod";
import session from "express-session";
import passport from "passport";
import { Strategy as GoogleStrategy } from "passport-google-oauth20";
import {
  baseEnvSchema,
  connectNats,
  createLogger,
  createPgPool,
  createRedis,
  loadEnv
} from "@cex/common";
import {
  verifyPassword,
  generateSessionId,
  getUserSessionCookieOptions
} from "@cex/security/auth";
import {
  registerUser,
  RegistrationError,
  normalizeEmail,
} from "./registration.js";

const env = loadEnv(
  baseEnvSchema.extend({
    SERVICE_NAME: z.string().default("auth-service"),
    GOOGLE_CLIENT_ID: z.string().optional(),
    GOOGLE_CLIENT_SECRET: z.string().optional(),
    GOOGLE_CALLBACK_URL: z.string().optional(),
    SESSION_SECRET: z.string().default("change-me-in-production"),
    FRONTEND_URL: z.string().default("http://trade.animica.org"),
  })
);

const logger = createLogger(env.SERVICE_NAME, env.LOG_LEVEL);

const start = async () => {
  const app = express();

  // Required when running behind TLS-terminating proxies (Nginx/Ingress) so secure session cookies are set.
  app.set("trust proxy", 1);
  
  // CORS configuration
  app.use((req, res, next) => {
    res.header("Access-Control-Allow-Origin", env.FRONTEND_URL);
    res.header("Access-Control-Allow-Credentials", "true");
    res.header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
    res.header("Access-Control-Allow-Headers", "Origin, X-Requested-With, Content-Type, Accept, Authorization");
    
    if (req.method === "OPTIONS") {
      return res.sendStatus(200);
    }
    next();
  });
  
  app.use(express.json());

  const pgPool = createPgPool(env);
  const redis = createRedis(env);
  const nats = await connectNats(env);

  // Session configuration
const sessionConfig = getUserSessionCookieOptions((process.env.NODE_ENV ?? "development") === "production");

app.use(session({
  secret: env.SESSION_SECRET,
  resave: false,
  saveUninitialized: false,
  cookie: {
    secure: sessionConfig.secure,
    httpOnly: sessionConfig.httpOnly,
    sameSite: sessionConfig.sameSite as any
  }
}) as any);
  // Passport configuration
  app.use(passport.initialize() as any);
  app.use(passport.session());

  // Configure Google OAuth if credentials are provided
  if (env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET) {
    const defaultGoogleCallbackUrl =
      (process.env.NODE_ENV ?? "development") === "production"
        ? "https://api.animica.io/api/v1/auth/google/callback"
        : "http://localhost:3000/api/v1/auth/google/callback";

    passport.use(new GoogleStrategy({
      clientID: env.GOOGLE_CLIENT_ID,
      clientSecret: env.GOOGLE_CLIENT_SECRET,
      callbackURL: env.GOOGLE_CALLBACK_URL || defaultGoogleCallbackUrl,
    }, async (accessToken, refreshToken, profile, done) => {
      try {
        // Find or create user based on Google ID
        const email = profile.emails?.[0]?.value;
        if (!email) {
          return done(new Error("No email from Google"), undefined);
        }

        const existingUser = await pgPool.query(
          "SELECT * FROM users WHERE google_id = $1 OR email = $2",
          [profile.id, email]
        );

        let user;
        if (existingUser.rows.length > 0) {
          user = existingUser.rows[0];
          
          // Update Google ID if not set
          if (!user.google_id) {
            await pgPool.query(
              "UPDATE users SET google_id = $1, oauth_provider = 'google', email_verified = true WHERE id = $2",
              [profile.id, user.id]
            );
          }
        } else {
          // Create new user
          const result = await pgPool.query(
            `INSERT INTO users (email, full_name, google_id, oauth_provider, email_verified, active) 
             VALUES ($1, $2, $3, 'google', true, true) 
             RETURNING *`,
            [email, profile.displayName || email, profile.id]
          );
          user = result.rows[0];
        }

        return done(null, user);
      } catch (error) {
        logger.error({ error }, "Google OAuth error");
        return done(error, undefined);
      }
    }));
  }

  passport.serializeUser((user: any, done) => {
    done(null, user.id);
  });

  passport.deserializeUser(async (id: string, done) => {
    try {
      const result = await pgPool.query("SELECT * FROM users WHERE id = $1", [id]);
      done(null, result.rows[0] || null);
    } catch (error) {
      done(error, null);
    }
  });

  const registerHandler = async (req: express.Request, res: express.Response) => {
    try {
      const user = await registerUser(pgPool, req.body);
      
      logger.info({ userId: user.id, email: user.email }, "User registered");

      res.status(201).json({
        message: "Registration successful. Please sign in.",
        userId: user.id,
        email: user.email,
        fullName: user.full_name,
      });
    } catch (error) {
      if (error instanceof RegistrationError) {
        const status = error.code === "email_taken" ? 409 : 400;
        return res.status(status).json({ message: error.message, code: error.code });
      }
      logger.error({ error }, "Registration error");
      res.status(500).json({ message: "Registration failed" });
    }
  };

  // Register endpoint
  app.post("/auth/register", registerHandler);
  app.post("/api/v1/auth/register", registerHandler);

  // Login endpoint
  const loginHandler = async (req: express.Request, res: express.Response) => {
    try {
      const { email, password } = req.body;

      if (!email || !password) {
        return res.status(400).json({ message: "Email and password are required" });
      }

      const normalizedEmail = normalizeEmail(email);

      // Find user
      const result = await pgPool.query(
        "SELECT id, email, full_name, password_hash, active FROM users WHERE lower(email) = lower($1)",
        [normalizedEmail]
      );

      if (result.rows.length === 0) {
        // Track failed attempt
        await pgPool.query(
          `INSERT INTO login_attempts (identifier, identifier_type, success, ip_address, failure_reason)
           VALUES ($1, 'email', false, $2, 'invalid_credentials')`,
          [normalizedEmail, req.ip || 'unknown']
        );
        return res.status(401).json({ message: "Invalid credentials" });
      }

      const user = result.rows[0];

      if (!user.active) {
        return res.status(403).json({ message: "Account is disabled" });
      }

      // Verify password
      if (!user.password_hash) {
        return res.status(401).json({ message: "Please use OAuth to sign in" });
      }

      const validPassword = await verifyPassword(user.password_hash, password);
      if (!validPassword) {
        // Track failed attempt
        await pgPool.query(
          `INSERT INTO login_attempts (identifier, identifier_type, success, ip_address, failure_reason)
           VALUES ($1, 'email', false, $2, 'invalid_password')`,
          [normalizedEmail, req.ip || 'unknown']
        );
        return res.status(401).json({ message: "Invalid credentials" });
      }

      // Generate session
      const sessionId = generateSessionId();
      
      // Update user's session
      await pgPool.query(
        "UPDATE users SET current_session_id = $1, last_login_at = NOW() WHERE id = $2",
        [sessionId, user.id]
      );

      // Track successful login
      await pgPool.query(
        `INSERT INTO login_attempts (identifier, identifier_type, success, ip_address)
         VALUES ($1, 'email', true, $2)`,
        [normalizedEmail, req.ip || 'unknown']
      );

      // Set session
      req.session.userId = user.id;
      req.session.sessionId = sessionId;

      logger.info({ userId: user.id, email }, "User logged in");

      res.json({
        message: "Login successful",
        userId: user.id,
        email: user.email,
        fullName: user.full_name
      });
    } catch (error) {
      logger.error({ error }, "Login error");
      res.status(500).json({ message: "Login failed" });
    }
  };
  app.post("/auth/login", loginHandler);
  app.post("/api/v1/auth/login", loginHandler);

  // Logout endpoint
  const logoutHandler = async (req: express.Request, res: express.Response) => {
    try {
      const userId = (req.session as any).userId;
      
      if (userId) {
        // Clear session from database
        await pgPool.query(
          "UPDATE users SET current_session_id = NULL WHERE id = $1",
          [userId]
        );
      }

      req.session.destroy((err) => {
        if (err) {
          logger.error({ error: err }, "Session destruction error");
        }
      });

      res.json({ message: "Logout successful" });
    } catch (error) {
      logger.error({ error }, "Logout error");
      res.status(500).json({ message: "Logout failed" });
    }
  };
  app.post("/auth/logout", logoutHandler);
  app.post("/api/v1/auth/logout", logoutHandler);

  // Google OAuth routes
  if (env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET) {
    const googleAuthStart = passport.authenticate("google", {
      scope: ["profile", "email"]
    });

    const googleAuthCallback = async (req: express.Request, res: express.Response) => {
      try {
        const user = req.user as any;

        // Generate session
        const sessionId = generateSessionId();

        // Update user's session
        await pgPool.query(
          "UPDATE users SET current_session_id = $1, last_login_at = NOW() WHERE id = $2",
          [sessionId, user.id]
        );

        // Set session
        (req.session as any).userId = user.id;
        (req.session as any).sessionId = sessionId;

        logger.info({ userId: user.id, email: user.email }, "User logged in via Google");

        // Redirect to frontend
        res.redirect(`${env.FRONTEND_URL}/markets`);
      } catch (error) {
        logger.error({ error }, "Google callback error");
        res.redirect(`${env.FRONTEND_URL}/login?error=oauth_failed`);
      }
    };

    app.get("/auth/google", googleAuthStart);
    app.get("/api/v1/auth/google", googleAuthStart);

    app.get("/auth/google/callback",
      passport.authenticate("google", { failureRedirect: `${env.FRONTEND_URL}/login?error=oauth_failed` }),
      googleAuthCallback
    );
    app.get("/api/v1/auth/google/callback",
      passport.authenticate("google", { failureRedirect: `${env.FRONTEND_URL}/login?error=oauth_failed` }),
      googleAuthCallback
    );
  } else {
    const oauthUnavailable = (_req: express.Request, res: express.Response) => {
      res.status(503).json({ message: "Google OAuth is not configured" });
    };
    app.get("/auth/google", oauthUnavailable);
    app.get("/api/v1/auth/google", oauthUnavailable);
    app.get("/auth/google/callback", oauthUnavailable);
    app.get("/api/v1/auth/google/callback", oauthUnavailable);
  }

  // Current user endpoint
  const currentUserHandler = async (req: express.Request, res: express.Response) => {
    try {
      const userId = (req.session as any).userId;
      
      if (!userId) {
        return res.status(401).json({ message: "Not authenticated" });
      }

      const result = await pgPool.query(
        "SELECT id, email, full_name, created_at FROM users WHERE id = $1",
        [userId]
      );

      if (result.rows.length === 0) {
        return res.status(404).json({ message: "User not found" });
      }

      res.json(result.rows[0]);
    } catch (error) {
      logger.error({ error }, "Get current user error");
      res.status(500).json({ message: "Failed to get user" });
    }
  };
  app.get("/auth/me", currentUserHandler);
  app.get("/api/v1/auth/me", currentUserHandler);

  const healthHandler = async (_req: express.Request, res: express.Response) => {
    const pgOk = await pgPool
      .query("SELECT 1")
      .then(() => true)
      .catch(() => false);
    const redisOk = await redis
      .ping()
      .then(() => true)
      .catch(() => false);
    res.json({
      status: "ok",
      service: env.SERVICE_NAME,
      postgres: pgOk,
      redis: redisOk,
      nats: nats.isClosed() ? "closed" : "open"
    });
  };

  // Support both /health and /healthz because existing infra probes and runbooks use /health.
  app.get("/health", healthHandler);
  app.get("/healthz", healthHandler);

  const server = app.listen(env.PORT, "0.0.0.0", () => {
    logger.info({ port: env.PORT }, "auth-service listening");
  });

  const shutdown = async () => {
    await nats.drain();
    await pgPool.end();
    redis.disconnect();
    server.close();
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
};

start().catch((error) => {
  logger.error({ error }, "failed to start auth-service");
  process.exit(1);
});
