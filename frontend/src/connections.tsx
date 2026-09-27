import { useCallback, useEffect, useRef, useState } from "react";
import {
  PlaidLinkError,
  PlaidLinkOnSuccess,
  PlaidLinkOptions,
  usePlaidLink,
} from "react-plaid-link";
import { FinanceNav } from "./navigation";

type ConnectionType = "banking" | "credit" | "investment";

type Connection = {
  id: string;
  display_name: string;
  connection_type: ConnectionType;
  institution_name: string;
  environment: string;
  status: string;
  last_checked_at: string;
};

type LinkSession = {
  token: string;
  connectionType: ConnectionType;
  displayName: string;
};

type McpGrant = {
  id: string;
  client_id: string;
  client_name: string;
  redirect_uri: string;
  scopes: string[];
  status: "active" | "revoked";
  created_at: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
};

const choices: Array<{
  type: ConnectionType;
  title: string;
  description: string;
  label: string;
}> = [
  {
    type: "banking",
    title: "Bank account",
    description: "Checking and savings balances, transactions, income deposits, and recurring expenses.",
    label: "Connect a bank",
  },
  {
    type: "credit",
    title: "Credit card",
    description: "Card balances, purchases, payments, and liabilities without double-counting transfers.",
    label: "Connect a credit card",
  },
  {
    type: "investment",
    title: "Investment or retirement",
    description: "Brokerage and retirement holdings, securities, valuations, and investment activity.",
    label: "Connect an investment account",
  },
];

function choiceFor(type: string | null) {
  return choices.find((choice) => choice.type === type);
}

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  let message = "The request could not be completed.";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") message = body.detail;
  } catch {
    // Preserve the safe generic message when a proxy returns non-JSON.
  }
  if (response.status === 401 || response.status === 403) {
    message = "Your secure session needs to be refreshed. Sign in again and retry.";
  }
  throw new Error(message);
}

function PlaidLauncher({
  session,
  finished,
  closed,
}: {
  session: LinkSession;
  finished: (message: string) => void;
  closed: (message?: string) => void;
}) {
  const [exchanging, setExchanging] = useState(false);

  const onSuccess = useCallback<PlaidLinkOnSuccess>(
    async (publicToken, metadata) => {
      if (!publicToken || !metadata.institution) {
        closed("Plaid completed without returning an institution. Please retry.");
        return;
      }
      setExchanging(true);
      try {
        const connection = await responseJson<Connection>(
          await fetch("/api/private/connections/plaid/exchange", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              public_token: publicToken,
              connection_type: session.connectionType,
              display_name: session.displayName,
              institution_id: metadata.institution.institution_id,
              institution_name: metadata.institution.name,
            }),
          }),
        );
        finished(`${metadata.institution.name} is connected. Its first records are importing in the background.`);
        let message = `${metadata.institution.name} is connected.`;
        try {
          await responseJson(
            await fetch(`/api/private/connections/${encodeURIComponent(connection.id)}/refresh`, {
              method: "POST",
            }),
          );
          message += " Its first data refresh is complete.";
        } catch {
          message += " Its first refresh will continue through the ingestion service.";
        }
        finished(message);
      } catch (error) {
        closed(error instanceof Error ? error.message : "The account could not be saved.");
      } finally {
        setExchanging(false);
      }
    },
    [closed, finished, session],
  );

  const onExit = useCallback(
    (error: PlaidLinkError | null) => {
      if (exchanging) return;
      closed(error ? "Plaid closed before the connection was completed. You can safely retry." : undefined);
    },
    [closed, exchanging],
  );

  const config: PlaidLinkOptions = { token: session.token, onSuccess, onExit };
  const { open, ready, error } = usePlaidLink(config);

  useEffect(() => {
    if (ready) open();
  }, [open, ready]);

  useEffect(() => {
    if (error) closed("Plaid Link could not load. Disable content blockers for this page and retry.");
  }, [closed, error]);

  return (
    <div className="plaid-launch-status" role="status">
      <span className="status-pulse" aria-hidden="true" />
      {exchanging ? "Encrypting the connection and importing its first records…" : "Opening Plaid’s secure connection window…"}
    </div>
  );
}

export function Connections() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [grants, setGrants] = useState<McpGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<string | null>(null);
  const [session, setSession] = useState<LinkSession | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const resumedConnection = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [connectionResult, grantResult] = await Promise.all([
        responseJson<Connection[]>(await fetch("/api/private/connections")),
        responseJson<{ grants: McpGrant[] }>(await fetch("/api/private/mcp/grants")),
      ]);
      setConnections(connectionResult);
      setGrants(grantResult.grants);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Connections could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (resumedConnection.current) return;
    resumedConnection.current = true;
    const pending = choiceFor(new URLSearchParams(location.search).get("connect"));
    if (!pending) return;
    history.replaceState(null, "", "/settings/connections");
    void begin(pending.type, pending.title);
  }, []);

  async function begin(type: ConnectionType, title: string) {
    setWorking(type);
    setMessage("");
    setError("");
    try {
      const result = await responseJson<{ link_token: string }>(
        await fetch("/api/private/connections/plaid/link-token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ connection_type: type, display_name: title }),
        }),
      );
      setSession({ token: result.link_token, connectionType: type, displayName: title });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "";
      setError(message === "Load failed" || message === "Failed to fetch"
        ? "The secure connection step was interrupted. Reload this page and try again."
        : message || "Plaid Link could not start.");
    } finally {
      setWorking(null);
    }
  }

  async function refresh(connection: Connection) {
    setWorking(connection.id);
    setMessage("");
    setError("");
    try {
      await responseJson(
        await fetch(`/api/private/connections/${encodeURIComponent(connection.id)}/refresh`, { method: "POST" }),
      );
      setMessage(`${connection.institution_name} is refreshed.`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Refresh failed.");
    } finally {
      setWorking(null);
    }
  }

  async function disconnect(connection: Connection) {
    if (!window.confirm(`Disconnect ${connection.institution_name}? Imported history will be retained.`)) return;
    setWorking(connection.id);
    setMessage("");
    setError("");
    try {
      await responseJson(
        await fetch(`/api/private/connections/${encodeURIComponent(connection.id)}`, { method: "DELETE" }),
      );
      setMessage(`${connection.institution_name} was disconnected. Its imported history was retained.`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Disconnect failed.");
    } finally {
      setWorking(null);
    }
  }

  async function revokeGrant(grant: McpGrant) {
    if (!window.confirm(`Revoke ${grant.client_name}? That client will immediately lose access and must be authorized again.`)) return;
    setWorking(`grant:${grant.id}`);
    setMessage("");
    setError("");
    try {
      await responseJson(
        await fetch(`/api/private/mcp/grants/${encodeURIComponent(grant.id)}`, { method: "DELETE" }),
      );
      setMessage(`${grant.client_name} was revoked.`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The client could not be revoked.");
    } finally {
      setWorking(null);
    }
  }

  const active = connections.filter((connection) => connection.status !== "disconnected");

  return (
    <main>
      <FinanceNav />
      <header className="dashboard-head connections-head">
        <a className="back-link" href="/dashboard">← Dashboard</a>
        <p className="kicker">Private sources & agent access</p>
        <h1>Control the connections that can see your data.</h1>
        <p className="lede">Financial providers connect through Plaid. AI clients receive separate, passkey-approved, revocable read-only grants—never your provider credentials or a permanent API key.</p>
      </header>

      {message && <p className="connection-notice" role="status">{message}</p>}
      {error && <p className="dashboard-error" role="alert">{error}</p>}

      <section className="connection-choices" aria-labelledby="add-source-title">
        <div className="section-title"><div><p className="eyebrow">Add a source</p><h2 id="add-source-title">Choose what the login contains</h2></div></div>
        <div className="connection-choice-grid">
          {choices.map((choice) => (
            <article className="connection-choice" key={choice.type}>
              <p className="eyebrow">{choice.type}</p>
              <h3>{choice.title}</h3>
              <p>{choice.description}</p>
              <button type="button" disabled={working !== null || session !== null} onClick={() => void begin(choice.type, choice.title)}>
                {working === choice.type ? "Preparing secure connection…" : choice.label}
              </button>
            </article>
          ))}
        </div>
        {session && (
          <PlaidLauncher
            session={session}
            finished={(notice) => { setSession(null); setMessage(notice); void load(); }}
            closed={(notice) => { setSession(null); if (notice) setError(notice); }}
          />
        )}
      </section>

      <section className="connected-sources" aria-labelledby="connected-title">
        <div className="section-title"><div><p className="eyebrow">Encrypted providers</p><h2 id="connected-title">Connected sources</h2></div><span>{active.length} active</span></div>
      {loading ? <p className="dashboard-loading" role="status">Loading connections…</p> : active.length === 0 ? (
          <div className="empty-signal"><strong>No accounts connected yet</strong><p>Start with the institution that contains your primary checking account.</p></div>
        ) : (
          <div className="connection-list">
            {active.map((connection) => (
              <article key={connection.id}>
                <div><p className="eyebrow">{connection.connection_type}</p><h3>{connection.institution_name}</h3><p>{connection.display_name} · {connection.environment}</p></div>
                <div className="connection-state"><span className={`freshness freshness-${connection.status === "healthy" ? "current" : "stale"}`}>{connection.status}</span><small>Checked {new Date(connection.last_checked_at).toLocaleString()}</small></div>
                <div className="connection-actions"><button type="button" disabled={working !== null} onClick={() => void refresh(connection)}>{working === connection.id ? "Working…" : "Refresh"}</button><button className="secondary-button" type="button" disabled={working !== null} onClick={() => void disconnect(connection)}>Disconnect</button></div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="connected-sources mcp-grants" aria-labelledby="mcp-grants-title">
        <div className="section-title"><div><p className="eyebrow">AI access</p><h2 id="mcp-grants-title">Named agent connections</h2></div><span>{grants.filter((grant) => grant.status === "active").length} active</span></div>
        <p className="section-intro">Each Codex or Claude installation should have its own grant. Summary access is scoped; revoke a connection at any time. Revocation requires a fresh passkey verification.</p>
        {loading ? <p className="dashboard-loading" role="status">Loading agent connections…</p> : grants.length === 0 ? (
          <div className="empty-signal"><strong>No agent connections authorized</strong><p>When you approve a Codex or Claude connection, it will appear here with its exact scopes.</p></div>
        ) : (
          <div className="connection-list">
            {grants.map((grant) => (
              <article key={grant.id}>
                <div><p className="eyebrow">{grant.status === "active" ? "active read-only grant" : "revoked grant"}</p><h3>{grant.client_name}</h3><p>{grant.scopes.join(" · ")}</p><small>Created {new Date(grant.created_at).toLocaleString()} · Last used {grant.last_used_at ? new Date(grant.last_used_at).toLocaleString() : "not yet"}</small></div>
                <div className="connection-state"><span className={`freshness freshness-${grant.status === "active" ? "current" : "stale"}`}>{grant.status}</span></div>
                <div className="connection-actions">{grant.status === "active" && <button className="secondary-button" type="button" disabled={working !== null} onClick={() => void revokeGrant(grant)}>{working === `grant:${grant.id}` ? "Revoking…" : "Revoke"}</button>}</div>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
