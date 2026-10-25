import { betterAuth } from "better-auth";
import { admin, jwt } from "better-auth/plugins";
import Database from "better-sqlite3";
import { Pool } from "pg";

/**
 * Better Auth server instance.
 *
 * - email/password sign-in with the admin plugin (roles: admin | ops | viewer)
 * - jwt plugin so the backend can verify identity via JWKS (/api/auth/jwks)
 *   and issue JWTs (/api/auth/token) for direct browser→backend calls
 * - Postgres (Neon) when DATABASE_URL is set, otherwise local SQLite so the
 *   demo still runs with zero external services
 */
const databaseUrl = process.env.DATABASE_URL;

const database = databaseUrl
  ? new Pool({ connectionString: databaseUrl, max: 1 })
  : new Database("./voicefirst.auth.db");

export const auth = betterAuth({
  database,
  secret: process.env.BETTER_AUTH_SECRET ?? "voicefirst-local-dev-secret-change-me",
  emailAndPassword: {
    enabled: true,
    autoSignIn: true,
  },
  user: {
    additionalFields: {
      role: {
        type: "string",
        required: false,
        defaultValue: "viewer",
        input: true,
      },
    },
  },
  // JWT payload carries the RBAC role; the control plane maps it to PolicyContext.
  jwt: {
    definePayload: (session: {
      user: { id: string; email: string; name: string; role?: string };
    }) => ({
      sub: session.user.id,
      email: session.user.email,
      name: session.user.name,
      role: session.user.role ?? "viewer",
    }),
  },
  plugins: [
    admin({
      defaultRole: "viewer",
      adminRole: "admin",
    }),
    jwt({
      // Local SQLite JWKS is encrypted with BETTER_AUTH_SECRET. If that secret
      // changes (or was never pinned), get-session 500s with a decrypt error.
      jwks: { disablePrivateKeyEncryption: true },
    }),
  ],
});
