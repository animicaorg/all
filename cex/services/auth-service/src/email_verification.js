import { createHash, randomBytes } from "node:crypto";
export function hashVerificationToken(token) {
    return createHash("sha256").update(token, "utf8").digest("hex");
}
export async function createEmailVerificationToken(pool, userId, email, ttlHours) {
    const token = randomBytes(32).toString("base64url");
    const tokenHash = hashVerificationToken(token);
    await pool.query(`
      UPDATE email_verification_tokens
      SET consumed_at = NOW()
      WHERE user_id = $1::uuid
        AND consumed_at IS NULL
    `, [userId]);
    await pool.query(`
      INSERT INTO email_verification_tokens (user_id, email, token_hash, expires_at)
      VALUES ($1::uuid, $2, $3, NOW() + ($4::text || ' hours')::interval)
    `, [userId, email, tokenHash, ttlHours]);
    return token;
}
export async function verifyEmailToken(pool, token) {
    const tokenHash = hashVerificationToken(token);
    const result = await pool.query(`
      SELECT
        email_verification_tokens.id::text AS token_id,
        email_verification_tokens.user_id::text AS user_id,
        email_verification_tokens.email,
        email_verification_tokens.expires_at,
        email_verification_tokens.consumed_at,
        users.email_verified
      FROM email_verification_tokens
      JOIN users ON users.id = email_verification_tokens.user_id
      WHERE email_verification_tokens.token_hash = $1
      LIMIT 1
    `, [tokenHash]);
    const row = result.rows[0];
    if (!row) {
        return { ok: false, code: "invalid_token", message: "Verification link is invalid." };
    }
    if (row.email_verified) {
        return { ok: false, code: "already_verified", message: "This email address is already verified." };
    }
    if (row.consumed_at || new Date(row.expires_at).getTime() <= Date.now()) {
        return { ok: false, code: "expired_token", message: "Verification link has expired. Request a new one." };
    }
    await pool.query(`
      UPDATE users
      SET email_verified = true,
          email_verified_at = NOW()
      WHERE id = $1::uuid
    `, [row.user_id]);
    await pool.query(`
      UPDATE email_verification_tokens
      SET consumed_at = NOW()
      WHERE user_id = $1::uuid
        AND consumed_at IS NULL
    `, [row.user_id]);
    return { ok: true, userId: row.user_id, email: row.email };
}
export async function findUserForVerificationEmail(pool, email) {
    const result = await pool.query(`
      SELECT id::text, email, full_name, active, email_verified
      FROM users
      WHERE lower(email) = lower($1)
      LIMIT 1
    `, [email]);
    return result.rows[0] ?? null;
}
