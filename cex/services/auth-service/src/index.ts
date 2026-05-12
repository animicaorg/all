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
import {
  createEmailVerificationToken,
  findUserForVerificationEmail,
  verifyEmailToken,
} from "./email_verification.js";
import {
  isSmtpConfigured,
  renderVerificationEmail,
  sendSmtpMail,
  type SmtpConfig,
} from "./smtp_mailer.js";

const env = loadEnv(
  baseEnvSchema.extend({
    SERVICE_NAME: z.string().default("auth-service"),
    GOOGLE_CLIENT_ID: z.string().optional(),
    GOOGLE_CLIENT_SECRET: z.string().optional(),
    GOOGLE_CALLBACK_URL: z.string().optional(),
    SESSION_SECRET: z.string().default("change-me-in-production"),
    FRONTEND_URL: z.string().default("http://trade.animica.org"),
    SMTP_HOST: z.string().optional(),
    SMTP_PORT: z.coerce.number().int().positive().default(587),
    SMTP_SECURE: z.enum(["true", "false"]).default("false").transform((value) => value === "true"),
    SMTP_FROM: z.string().optional(),
    SMTP_USER: z.string().optional(),
    SMTP_PASSWORD: z.string().optional(),
    SMTP_PASS: z.string().optional(),
    EMAIL_VERIFICATION_REQUIRED: z
      .enum(["true", "false"])
      .default("true")
      .transform((value) => value === "true"),
    EMAIL_VERIFICATION_TTL_HOURS: z.coerce.number().int().positive().default(24),
  })
);

const logger = createLogger(env.SERVICE_NAME, env.LOG_LEVEL);

function normalizeSmtpPassword(host: string | undefined, password: string | undefined): string | undefined {
  const trimmed = password?.trim();
  if (!trimmed) return undefined;
  return host && /(^|\.)gmail\.com$/i.test(host) ? trimmed.replace(/\s+/g, "") : trimmed;
}

function serializeError(error: unknown) {
  if (error instanceof Error) {
    return { name: error.name, message: error.message, stack: error.stack };
  }
  return { message: String(error) };
}

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
  const smtpConfig: SmtpConfig = {
    host: env.SMTP_HOST,
    port: env.SMTP_PORT,
    secure: env.SMTP_SECURE,
    user: env.SMTP_USER,
    password: normalizeSmtpPassword(env.SMTP_HOST, env.SMTP_PASSWORD || env.SMTP_PASS),
    from: env.SMTP_FROM || env.SMTP_USER || "animicaorg@gmail.com",
  };

  const verificationUrl = (token: string) => {
    const url = new URL("/verify-email", env.FRONTEND_URL);
    url.searchParams.set("token", token);
    return url.toString();
  };

  const sendVerificationEmail = async (user: { id: string; email: string; full_name?: string | null }) => {
    const token = await createEmailVerificationToken(
      pgPool,
      user.id,
      user.email,
      env.EMAIL_VERIFICATION_TTL_HOURS
    );
    const rendered = renderVerificationEmail({
      fullName: user.full_name,
      verificationUrl: verificationUrl(token),
      expiresHours: env.EMAIL_VERIFICATION_TTL_HOURS,
    });

    await sendSmtpMail(smtpConfig, {
      to: user.email,
      subject: "Verify your Animica Exchange email",
      html: rendered.html,
      text: rendered.text,
    });
  };

  // Session configuration
const isProduction = (process.env.NODE_ENV ?? "development") === "production";

app.use(session({
  secret: env.SESSION_SECRET,
  resave: false,
  saveUninitialized: false,
  proxy: true,
  name: "animica.sid",
  cookie: {
    secure: isProduction,
    httpOnly: true,
    sameSite: isProduction ? "none" : "lax",
    maxAge: 1000 * 60 * 60 * 24 * 7
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
          
          // Google sign-in confirms control of the mailbox for this account.
          if (!user.google_id || !user.email_verified) {
            await pgPool.query(
              `UPDATE users
               SET google_id = $1,
                   oauth_provider = 'google',
                   email_verified = true,
                   email_verified_at = COALESCE(email_verified_at, NOW())
               WHERE id = $2`,
              [profile.id, user.id]
            );
          }
        } else {
          // Create new user
          const result = await pgPool.query(
            `INSERT INTO users (email, full_name, google_id, oauth_provider, email_verified, email_verified_at, active) 
             VALUES ($1, $2, $3, 'google', true, NOW(), true) 
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
      if (env.EMAIL_VERIFICATION_REQUIRED && !isSmtpConfigured(smtpConfig)) {
        return res.status(503).json({
          code: "smtp_not_configured",
          message: "Email verification is required, but SMTP is not configured.",
        });
      }

      const user = await registerUser(pgPool, {
        ...req.body,
        ipAddress: req.ip || null,
        userAgent: req.get("user-agent") || null,
        deviceFingerprint:
          typeof req.body?.deviceFingerprint === "string" ? req.body.deviceFingerprint : null,
      });

      if (env.EMAIL_VERIFICATION_REQUIRED) {
        try {
          await sendVerificationEmail(user);
        } catch (error) {
          logger.error({ error: serializeError(error), userId: user.id }, "Verification email send failed");
          return res.status(502).json({
            code: "verification_email_failed",
            message:
              "Account created, but the verification email could not be sent. Use resend verification or contact support.",
            userId: user.id,
            email: user.email,
          });
        }
      }
      
      logger.info({ userId: user.id, email: user.email }, "User registered");

      res.status(201).json({
        message: env.EMAIL_VERIFICATION_REQUIRED
          ? "Registration successful. Check your email to verify your account."
          : "Registration successful. Please sign in.",
        userId: user.id,
        email: user.email,
        fullName: user.full_name,
        verificationRequired: env.EMAIL_VERIFICATION_REQUIRED,
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

  const verifyEmailHandler = async (req: express.Request, res: express.Response) => {
    try {
      const token =
        typeof req.body?.token === "string"
          ? req.body.token.trim()
          : typeof req.query.token === "string"
            ? req.query.token.trim()
            : "";

      if (!token) {
        return res.status(400).json({ code: "missing_token", message: "Verification token is required" });
      }

      const result = await verifyEmailToken(pgPool, token);
      if (result.ok) {
        logger.info({ userId: result.userId, email: result.email }, "User email verified");
        if (req.method === "GET") {
          return res.redirect(`${env.FRONTEND_URL}/verify-email?status=verified`);
        }
        return res.json({ verified: true, email: result.email });
      }

      if (result.code === "already_verified") {
        if (req.method === "GET") {
          return res.redirect(`${env.FRONTEND_URL}/verify-email?status=verified`);
        }
        return res.json({ verified: true, message: result.message });
      }

      const status = result.code === "expired_token" ? 410 : 400;
      return res.status(status).json({ verified: false, code: result.code, message: result.message });
    } catch (error) {
      logger.error({ error }, "Email verification error");
      res.status(500).json({ message: "Email verification failed" });
    }
  };

  const resendVerificationHandler = async (req: express.Request, res: express.Response) => {
    try {
      const schema = z.object({ email: z.string().email() });
      const { email } = schema.parse(req.body);
      const normalizedEmail = normalizeEmail(email);
      const user = await findUserForVerificationEmail(pgPool, normalizedEmail);

      const genericResponse = {
        message: "If that email has an unverified Animica Exchange account, a new verification email has been sent.",
      };

      if (!user || !user.active || user.email_verified) {
        return res.json(genericResponse);
      }

      if (!isSmtpConfigured(smtpConfig)) {
        return res.status(503).json({
          code: "smtp_not_configured",
          message: "Email verification is required, but SMTP is not configured.",
        });
      }

      await sendVerificationEmail(user);
      logger.info({ userId: user.id, email: user.email }, "Verification email resent");
      return res.json(genericResponse);
    } catch (error) {
      if (error instanceof z.ZodError) {
        return res.status(400).json({ code: "invalid_input", message: "A valid email address is required." });
      }
      logger.error({ error: serializeError(error) }, "Resend verification email error");
      res.status(500).json({ message: "Failed to resend verification email" });
    }
  };

  app.get("/auth/verify-email", verifyEmailHandler);
  app.post("/auth/verify-email", verifyEmailHandler);
  app.post("/auth/resend-verification", resendVerificationHandler);
  app.get("/api/v1/auth/verify-email", verifyEmailHandler);
  app.post("/api/v1/auth/verify-email", verifyEmailHandler);
  app.post("/api/v1/auth/resend-verification", resendVerificationHandler);

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
        "SELECT id, email, full_name, password_hash, active, email_verified FROM users WHERE lower(email) = lower($1)",
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

      // TEMP REPAIR: allow existing unverified users to sign in so exchange
      // account endpoints keep working after SMTP rollout. Frontend still gets
      // emailVerified so it can prompt for verification without breaking auth.

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
        fullName: user.full_name,
        emailVerified: !!user.email_verified,
        verificationRequired: !!env.EMAIL_VERIFICATION_REQUIRED
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
        "SELECT id, email, full_name, created_at, active, email_verified, email_verified_at FROM users WHERE id = $1",
        [userId]
      );

      if (result.rows.length === 0) {
        return res.status(404).json({ message: "User not found" });
      }

      const user = result.rows[0];
      if (!user.active) {
        return res.status(403).json({ message: "Account is disabled" });
      }
      // TEMP REPAIR: do not destroy active sessions for unverified legacy users.
      // Return user info and emailVerified flag so the frontend can continue
      // authenticated account calls and show verification UI separately.

      res.json({
        id: user.id,
        email: user.email,
        full_name: user.full_name,
        created_at: user.created_at,
        emailVerified: user.email_verified,
        emailVerifiedAt: user.email_verified_at,
      });
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
