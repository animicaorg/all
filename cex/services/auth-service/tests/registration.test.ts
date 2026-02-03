import { test } from "node:test";
import assert from "node:assert/strict";
import { registerUser } from "../src/registration.js";

function createMockPool() {
  const users: Array<{ id: string; email: string }> = [];

  return {
    users,
    async query(sql: string, params: any[] = []) {
      if (sql.startsWith("SELECT id FROM users")) {
        const email = params[0]?.toLowerCase();
        const found = users.filter((user) => user.email.toLowerCase() === email);
        return { rows: found, rowCount: found.length };
      }

      if (sql.startsWith("INSERT INTO users")) {
        const user = {
          id: `user-${users.length + 1}`,
          email: params[0],
          full_name: params[1],
          created_at: new Date().toISOString(),
        };
        users.push({ id: user.id, email: user.email });
        return { rows: [user], rowCount: 1 };
      }

      return { rows: [], rowCount: 0 };
    },
  };
}

test("registerUser creates a user and rejects duplicate emails", async () => {
  const pool = createMockPool();

  const first = await registerUser(pool as any, {
    email: "user@example.com",
    password: "Secure12345",
    fullName: "Test User",
  });

  assert.equal(first.email, "user@example.com");

  await assert.rejects(
    registerUser(pool as any, {
      email: "USER@example.com",
      password: "Secure12345",
      fullName: "Duplicate User",
    }),
    (err: any) => {
      assert.equal(err.code, "email_taken");
      return true;
    }
  );
});
