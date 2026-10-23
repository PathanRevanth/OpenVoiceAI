"use client";

import { createContext, useContext, useEffect } from "react";
import { useRouter } from "next/navigation";
import { authClient } from "@/lib/auth-client";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role?: string;
}

type AuthValue = {
  user: AuthUser | null;
  loading: boolean;
  signOut: () => Promise<void>;
};

const Ctx = createContext<AuthValue>({
  user: null,
  loading: true,
  signOut: async () => {},
});

export function useAuthValue() {
  return useContext(Ctx);
}

/** Guards pages: redirects to /login when signed out. */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data, isPending } = authClient.useSession();

  useEffect(() => {
    if (!isPending && !data) router.replace("/login");
  }, [isPending, data, router]);

  const signOut = async () => {
    await authClient.signOut();
    router.replace("/login");
  };

  const user: AuthUser | null = data
    ? {
        id: data.user.id,
        email: data.user.email,
        name: data.user.name,
        role: (data.user as { role?: string }).role,
      }
    : null;

  return (
    <Ctx.Provider value={{ user, loading: isPending, signOut }}>
      {children}
    </Ctx.Provider>
  );
}
