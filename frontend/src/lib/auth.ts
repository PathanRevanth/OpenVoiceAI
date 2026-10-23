import { betterAuth } from "better-auth";
import { admin, jwt } from "better-auth/plugins";
import Database from "better-sqlite3";

/**
 * Better Auth server instance.
 *
 * - email/password sign-in with the admin plugin (roles: admin | ops | viewer)
 * - jwt plugin so the backend can verify identity via JWKS (/api/auth/jwks)
 *   and issue JWTs (/api/auth/token) for direct browser→backend calls
 * - SQLite storage so the demo runs with zero external services
 */
export const auth = betterAuth({
  database: new Database("./voicefirst.auth.db"),
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
    jwt(),
  ],
});
