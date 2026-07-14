import type { DefaultSession } from "next-auth";

// Module augmentation — adds the short-lived FastAPI bearer token
// (`apiToken`) onto NextAuth's Session/JWT types. See lib/auth.ts for how
// it's minted and lib/api.ts for how it's attached to backend requests.
declare module "next-auth" {
  interface Session extends DefaultSession {
    apiToken?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    apiToken?: string;
  }
}
