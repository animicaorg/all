import {
  CanActivate,
  ExecutionContext,
  Injectable,
  SetMetadata,
  UnauthorizedException,
  ForbiddenException,
} from "@nestjs/common";
import { Reflector } from "@nestjs/core";
import { JwtService } from "@nestjs/jwt";
import type { Request } from "express";
import type { UserRole } from "@animica/shared";

export const COOKIE_NAME = "animica_pool_session";
export const ROLES_KEY = "roles";
export const PUBLIC_KEY = "isPublic";

export const Public = () => SetMetadata(PUBLIC_KEY, true);
export const Roles = (...roles: UserRole[]) => SetMetadata(ROLES_KEY, roles);

export interface AuthUser {
  id: string;
  email: string;
  role: UserRole;
}

declare module "express" {
  interface Request {
    user?: AuthUser;
  }
}

@Injectable()
export class AuthGuard implements CanActivate {
  constructor(private readonly jwt: JwtService, private readonly reflector: Reflector) {}

  async canActivate(ctx: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.getAllAndOverride<boolean>(PUBLIC_KEY, [
      ctx.getHandler(),
      ctx.getClass(),
    ]);
    const req = ctx.switchToHttp().getRequest<Request>();
    const token = extractToken(req);
    if (token) {
      try {
        const payload = await this.jwt.verifyAsync<AuthUser>(token);
        req.user = { id: payload.id, email: payload.email, role: payload.role };
      } catch {
        /* invalid token — treated as anonymous */
      }
    }
    if (isPublic) return true;
    if (!req.user) throw new UnauthorizedException("Authentication required");

    const roles = this.reflector.getAllAndOverride<UserRole[]>(ROLES_KEY, [
      ctx.getHandler(),
      ctx.getClass(),
    ]);
    if (roles && roles.length > 0 && !roles.includes(req.user.role)) {
      throw new ForbiddenException("Insufficient role");
    }
    return true;
  }
}

function extractToken(req: Request): string | null {
  const cookie = (req.cookies as Record<string, string> | undefined)?.[COOKIE_NAME];
  if (cookie) return cookie;
  const auth = req.headers.authorization;
  if (auth?.startsWith("Bearer ")) return auth.slice(7);
  return null;
}
