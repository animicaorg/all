// Augments Express's Request via the canonical `Express` namespace.
// This is the supported pattern (express-serve-static-core re-exports
// the namespace's Request type) and works without consumers having to
// import this file explicitly.

declare global {
  namespace Express {
    interface Request {
      user?: import('@prisma/client').User;
      session?: { id: string; expiresAt: Date };
      activeSubscription?: import('@prisma/client').Prisma.UserSubscriptionGetPayload<{
        include: { plan: true };
      }>;
    }
  }
}

export {};
