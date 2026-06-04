import { Injectable, ConflictException, UnauthorizedException } from "@nestjs/common";
import { JwtService } from "@nestjs/jwt";
import bcrypt from "bcryptjs";
import { randomBytes } from "node:crypto";
import { PrismaService } from "../prisma/prisma.service";
import { adminEmails } from "../config/env";
import { auditLog } from "../common/audit";
import type { RegisterDto, LoginDto } from "./auth.dto";
import type { AuthUser } from "../common/auth.guard";

@Injectable()
export class AuthService {
  constructor(private readonly prisma: PrismaService, private readonly jwt: JwtService) {}

  private async sign(user: { id: string; email: string; role: string }): Promise<string> {
    const payload: AuthUser = { id: user.id, email: user.email, role: user.role as AuthUser["role"] };
    return this.jwt.signAsync(payload, { expiresIn: "30d" });
  }

  async register(dto: RegisterDto): Promise<{ token: string; user: AuthUser }> {
    const email = dto.email.toLowerCase();
    const existing = await this.prisma.user.findUnique({ where: { email } });
    if (existing) throw new ConflictException("Email already registered");

    const passwordHash = await bcrypt.hash(dto.password, 12);
    const role = adminEmails().has(email) ? "ADMIN" : "CUSTOMER";

    let referredById: string | undefined;
    if (dto.referralCode) {
      const referrer = await this.prisma.user.findUnique({ where: { referralCode: dto.referralCode } });
      referredById = referrer?.id;
    }

    const user = await this.prisma.user.create({
      data: {
        email,
        passwordHash,
        displayName: dto.displayName,
        role,
        referralCode: `anm_${randomBytes(5).toString("hex")}`,
        referredById,
      },
    });
    await auditLog(this.prisma, { action: "auth.register", entityType: "User", entityId: user.id, actor: email });
    const token = await this.sign(user);
    return { token, user: { id: user.id, email: user.email, role: user.role as AuthUser["role"] } };
  }

  async login(dto: LoginDto): Promise<{ token: string; user: AuthUser }> {
    const email = dto.email.toLowerCase();
    const user = await this.prisma.user.findUnique({ where: { email } });
    if (!user || !user.isActive) throw new UnauthorizedException("Invalid credentials");
    const ok = await bcrypt.compare(dto.password, user.passwordHash);
    if (!ok) throw new UnauthorizedException("Invalid credentials");

    // Promote to ADMIN if listed in ADMIN_EMAILS (idempotent).
    let role = user.role;
    if (adminEmails().has(email) && role !== "ADMIN") {
      await this.prisma.user.update({ where: { id: user.id }, data: { role: "ADMIN" } });
      role = "ADMIN";
    }
    const token = await this.sign({ ...user, role });
    return { token, user: { id: user.id, email: user.email, role: role as AuthUser["role"] } };
  }

  async me(userId: string) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        id: true, email: true, role: true, displayName: true, isActive: true,
        kycStatus: true, referralCode: true, walletAddress: true, createdAt: true,
      },
    });
    return user;
  }
}
