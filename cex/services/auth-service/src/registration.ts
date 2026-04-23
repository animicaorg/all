import { z } from "zod";
import { hashPassword } from "@cex/security/auth";

export interface RegistrationInput {
  email: string;
  password: string;
  fullName: string;
}

export interface PgPoolLike {
  query: (sql: string, params?: any[]) => Promise<{ rows: any[]; rowCount?: number }>;
}

export class RegistrationError extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

const registrationSchema = z.object({
  email: z.string().email(),
  password: z
    .string()
    .min(10)
    .refine((value) => /[A-Za-z]/.test(value), { message: "Password must include a letter" })
    .refine((value) => /\d/.test(value), { message: "Password must include a number" }),
  fullName: z.string().min(1),
});

export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

export function validateRegistrationInput(input: RegistrationInput): RegistrationInput {
  const result = registrationSchema.safeParse(input);
  if (!result.success) {
    const message = result.error.issues[0]?.message ?? "Invalid registration input";
    throw new RegistrationError("invalid_input", message);
  }
  return result.data;
}

export async function registerUser(pool: PgPoolLike, input: RegistrationInput) {
  const validated = validateRegistrationInput(input);
  const email = normalizeEmail(validated.email);

  const existing = await pool.query("SELECT id FROM users WHERE lower(email) = lower($1)", [email]);
  if (existing.rows.length > 0) {
    throw new RegistrationError("email_taken", "Email is already registered");
  }

  const passwordHash = await hashPassword(validated.password);

  const result = await pool.query(
    `INSERT INTO users (email, full_name, password_hash, active, email_verified) 
     VALUES ($1, $2, $3, true, false) 
     RETURNING id, email, full_name, created_at`,
    [email, validated.fullName.trim(), passwordHash]
  );

  return result.rows[0];
}
