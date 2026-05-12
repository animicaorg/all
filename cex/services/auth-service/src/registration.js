import { randomBytes } from "node:crypto";
import { z } from "zod";
import { hashPassword } from "@cex/security/auth";
export class RegistrationError extends Error {
    constructor(code, message) {
        super(message);
        this.code = code;
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
    referralCode: z.string().max(64).optional(),
    ipAddress: z.string().max(128).nullable().optional(),
    userAgent: z.string().max(512).nullable().optional(),
    deviceFingerprint: z.string().max(256).nullable().optional(),
});
const REFERRAL_REWARD_ATOMS = process.env.REFERRAL_REWARD_ATOMS || "100000000000";
const REFERRAL_ASSET = "ANM";
const REFERRAL_POOL_ACCOUNT_ID = "system:airdrop";
const REFERRAL_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const ANM_DECIMALS = 9;
export function normalizeEmail(email) {
    return email.trim().toLowerCase();
}
export function validateRegistrationInput(input) {
    const result = registrationSchema.safeParse(input);
    if (!result.success) {
        const message = result.error.issues[0]?.message ?? "Invalid registration input";
        throw new RegistrationError("invalid_input", message);
    }
    return result.data;
}
function normalizeReferralCode(code) {
    const normalized = code?.trim().toUpperCase().replace(/[^A-Z0-9]/g, "") ?? "";
    return normalized.length > 0 ? normalized : null;
}
function generateReferralCode() {
    const bytes = randomBytes(8);
    let code = "";
    for (const byte of bytes) {
        code += REFERRAL_CODE_ALPHABET[byte % REFERRAL_CODE_ALPHABET.length];
    }
    return code;
}
function atomsToDecimal(atoms, decimals) {
    const value = typeof atoms === "bigint" ? atoms : BigInt(atoms || "0");
    const raw = value.toString().padStart(decimals + 1, "0");
    const whole = raw.slice(0, -decimals) || "0";
    const fraction = raw.slice(-decimals).replace(/0+$/, "");
    return `${whole}${fraction ? `.${fraction}` : ""}`;
}
function asAtomBigInt(value) {
    return BigInt(String(value ?? "0"));
}
async function withTransaction(pool, callback) {
    const client = pool.connect ? await pool.connect() : pool;
    try {
        await client.query("BEGIN");
        const result = await callback(client);
        await client.query("COMMIT");
        return result;
    }
    catch (error) {
        await client.query("ROLLBACK").catch(() => undefined);
        throw error;
    }
    finally {
        client.release?.();
    }
}
async function ensureReferralCode(client, userId) {
    const existing = await client.query("SELECT code FROM referral_codes WHERE user_id = $1::uuid AND active = true LIMIT 1", [userId]);
    if (existing.rows[0]?.code) {
        return String(existing.rows[0].code);
    }
    for (let attempt = 0; attempt < 8; attempt += 1) {
        const code = generateReferralCode();
        try {
            const result = await client.query(`
          INSERT INTO referral_codes (user_id, code, active)
          VALUES ($1::uuid, $2, true)
          ON CONFLICT (user_id)
          DO UPDATE SET active = true, updated_at = NOW()
          RETURNING code
        `, [userId, code]);
            return String(result.rows[0]?.code ?? code);
        }
        catch (error) {
            if (error?.code !== "23505")
                throw error;
        }
    }
    throw new RegistrationError("referral_code_failed", "Could not create referral code");
}
async function findReferrer(client, referralCode) {
    const result = await client.query(`
      SELECT user_id::text, code
      FROM referral_codes
      WHERE UPPER(code) = UPPER($1)
        AND active = true
      LIMIT 1
    `, [referralCode]);
    return result.rows[0] ?? null;
}
async function ensureAirdropPool(client) {
    await client.query(`
      INSERT INTO airdrop_settings (
        id, asset, claim_amount, claim_amount_atoms, cooldown_seconds, enabled, pool_account_id
      )
      VALUES ('default', $1, 1, 1000000000, 14400, true, $2)
      ON CONFLICT (id) DO NOTHING
    `, [REFERRAL_ASSET, REFERRAL_POOL_ACCOUNT_ID]);
    await client.query(`
      INSERT INTO balances (account_id, asset, available, locked, available_atoms, locked_atoms, updated_at)
      VALUES ($1, $2, 0, 0, 0, 0, NOW())
      ON CONFLICT (account_id, asset) DO NOTHING
    `, [REFERRAL_POOL_ACCOUNT_ID, REFERRAL_ASSET]);
}
async function creditSignupReferralReward(client, referral) {
    await ensureAirdropPool(client);
    const pool = await client.query(`
      SELECT available_atoms
      FROM balances
      WHERE account_id = $1
        AND asset = $2
      FOR UPDATE
    `, [REFERRAL_POOL_ACCOUNT_ID, REFERRAL_ASSET]);
    const rewardAtoms = REFERRAL_REWARD_ATOMS;
    const totalRewardAtoms = asAtomBigInt(rewardAtoms) * 2n;
    if (asAtomBigInt(pool.rows[0]?.available_atoms) < totalRewardAtoms) {
        for (const reward of [
            { role: "referrer", userId: referral.referrerUserId },
            { role: "referred", userId: referral.referredUserId },
        ]) {
            await client.query(`
          INSERT INTO referral_reward_events (
            referral_id, amount_atoms, asset, source, status, reward_role, recipient_user_id, metadata
          )
          VALUES ($1::uuid, $2::numeric, $3, 'airdrop_pool', 'insufficient_pool', $4, $5::uuid, $6::jsonb)
        `, [
                referral.id,
                rewardAtoms,
                REFERRAL_ASSET,
                reward.role,
                reward.userId,
                JSON.stringify({ attemptedAt: new Date().toISOString(), referralCode: referral.code }),
            ]);
        }
        await client.query(`
        UPDATE referrals
        SET status = 'pending_insufficient_pool',
            qualification_reason = 'airdrop_pool_insufficient',
            updated_at = NOW()
        WHERE id = $1::uuid
      `, [referral.id]);
        return;
    }
    await client.query(`
      UPDATE balances
      SET available = available - $3::numeric,
          available_atoms = available_atoms - $4::numeric,
          updated_at = NOW()
      WHERE account_id = $1
        AND asset = $2
    `, [
        REFERRAL_POOL_ACCOUNT_ID,
        REFERRAL_ASSET,
        atomsToDecimal(totalRewardAtoms, ANM_DECIMALS),
        totalRewardAtoms.toString(),
    ]);
    for (const reward of [
        { role: "referrer", userId: referral.referrerUserId },
        { role: "referred", userId: referral.referredUserId },
    ]) {
        await client.query(`
        INSERT INTO balances (account_id, asset, available, locked, available_atoms, locked_atoms, updated_at)
        VALUES ($1, $2, $3::numeric, 0, $4::numeric, 0, NOW())
        ON CONFLICT (account_id, asset)
        DO UPDATE SET
          available = balances.available + EXCLUDED.available,
          available_atoms = balances.available_atoms + EXCLUDED.available_atoms,
          updated_at = NOW()
      `, [`user:${reward.userId}`, REFERRAL_ASSET, atomsToDecimal(rewardAtoms, ANM_DECIMALS), rewardAtoms]);
        await client.query(`
        INSERT INTO referral_reward_events (
          referral_id, amount_atoms, asset, source, status, reward_role, recipient_user_id, metadata
        )
        VALUES ($1::uuid, $2::numeric, $3, 'airdrop_pool', 'credited', $4, $5::uuid, $6::jsonb)
        ON CONFLICT DO NOTHING
      `, [
            referral.id,
            rewardAtoms,
            REFERRAL_ASSET,
            reward.role,
            reward.userId,
            JSON.stringify({ creditedAt: new Date().toISOString(), referralCode: referral.code }),
        ]);
    }
    await client.query(`
      UPDATE referrals
      SET status = 'rewarded',
          qualification_reason = 'signup_rewarded',
          rewarded_at = COALESCE(rewarded_at, NOW()),
          referred_rewarded_at = COALESCE(referred_rewarded_at, NOW()),
          updated_at = NOW()
      WHERE id = $1::uuid
    `, [referral.id]);
}
export async function registerUser(pool, input) {
    const validated = validateRegistrationInput(input);
    const email = normalizeEmail(validated.email);
    const referralCode = normalizeReferralCode(validated.referralCode);
    const passwordHash = await hashPassword(validated.password);
    return withTransaction(pool, async (client) => {
        const existing = await client.query("SELECT id FROM users WHERE lower(email) = lower($1)", [email]);
        if (existing.rows.length > 0) {
            throw new RegistrationError("email_taken", "Email is already registered");
        }
        const referrer = referralCode ? await findReferrer(client, referralCode) : null;
        if (referralCode && !referrer) {
            throw new RegistrationError("invalid_referral_code", "Referral code is invalid");
        }
        const result = await client.query(`INSERT INTO users (email, full_name, password_hash, active, email_verified)
       VALUES ($1, $2, $3, true, false)
       RETURNING id, email, full_name, created_at`, [email, validated.fullName.trim(), passwordHash]);
        const user = result.rows[0];
        await ensureReferralCode(client, user.id);
        if (referrer) {
            if (String(referrer.user_id) === String(user.id)) {
                throw new RegistrationError("invalid_referral_code", "Referral code cannot refer to the same account");
            }
            const referralResult = await client.query(`
          INSERT INTO referrals (
            referrer_user_id,
            referred_user_id,
            referral_code,
            status,
            qualification_reason,
            reward_atoms,
            referred_reward_atoms,
            ip_address,
            user_agent,
            device_fingerprint,
            metadata
          )
          VALUES (
            $1::uuid,
            $2::uuid,
            $3,
            'pending',
            'signup_created',
            $4::numeric,
            $4::numeric,
            $5,
            $6,
            $7,
            $8::jsonb
          )
          RETURNING id::text
        `, [
                referrer.user_id,
                user.id,
                referrer.code ?? referralCode,
                REFERRAL_REWARD_ATOMS,
                validated.ipAddress ?? null,
                validated.userAgent ?? null,
                validated.deviceFingerprint ?? null,
                JSON.stringify({ signupReward: true }),
            ]);
            const referralId = referralResult.rows[0]?.id;
            if (referralId) {
                await creditSignupReferralReward(client, {
                    id: referralId,
                    referrerUserId: String(referrer.user_id),
                    referredUserId: String(user.id),
                    code: String(referrer.code ?? referralCode),
                });
            }
        }
        return user;
    });
}
