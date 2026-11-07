"use client";

import { createContext, useContext, useEffect, useState } from "react";

import type { CurrentUser } from "@/lib/types";

const AccountContext = createContext<CurrentUser | null>(null);
export const useAccount = () => useContext(AccountContext);

export function AccountProvider({ account, children }: {
  account: CurrentUser; children: React.ReactNode;
}) {
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    const expire = () => { setExpired(true); window.location.replace("/login?expired=1"); };
    window.addEventListener("flowmedic:session-expired", expire);
    return () => window.removeEventListener("flowmedic:session-expired", expire);
  }, []);
  if (expired) return <p role="status">Session expired. Opening login…</p>;
  return <AccountContext.Provider value={account}>{children}</AccountContext.Provider>;
}
