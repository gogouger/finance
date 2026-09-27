import { useEffect, useState } from "react";

export type FinanceSession = {
  authenticated: boolean;
  fresh: boolean;
};

const signedOut: FinanceSession = { authenticated: false, fresh: false };

export async function readFinanceSession(): Promise<FinanceSession> {
  const response = await fetch("/oauth2/session", {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) return signedOut;
  return response.json() as Promise<FinanceSession>;
}

export function useFinanceSession() {
  const [session, setSession] = useState<FinanceSession | null>(null);

  useEffect(() => {
    let active = true;
    readFinanceSession()
      .then((value) => { if (active) setSession(value); })
      .catch(() => { if (active) setSession(signedOut); });
    return () => { active = false; };
  }, []);

  return session;
}

export function FinanceNav() {
  const session = useFinanceSession();
  const authenticated = session?.authenticated === true;

  return <>
    <nav aria-label="Primary navigation">
      <a className="brand" href="/" aria-label="Finance home"><small>GG /</small> Finance</a>
      <div className="nav-links">
        {authenticated ? <>
          <a href="/dashboard">Dashboard</a>
          <a href="/settings/connections">Connections</a>
        </> : <a href="/preview">Dashboard demo</a>}
        <a href="/housing">House</a>
        <a href="/retirement">Retirement</a>
        <a className="nav-portfolio" href="https://gordongouger.com/projects.html">All projects</a>
        {authenticated ? <form method="post" action="/oauth2/logout"><button type="submit">Sign out</button></form> : <a className="nav-primary" href="/dashboard">Owner sign in</a>}
      </div>
    </nav>
    <span id="main-content" tabIndex={-1} />
  </>;
}
