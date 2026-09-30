/**
 * A screen that fails shows what happened instead of taking the window with it (B47).
 * React removes everything on an error it doesn't catch, which left a blank window;
 * with this, only the failed screen is replaced, the sidebar and top bar keep working,
 * and the error goes to the engine's log.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { api } from "../api";
import { reportError } from "../report";
import { Icon } from "./Icon";

interface Props {
  children: ReactNode;
  /** "screen": one screen, inside the app's frame; "app": the whole window */
  where: "screen" | "app";
}

interface State {
  error?: Error;
  component?: string;
  note?: string;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = {};

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.setState({ component: info.componentStack ?? undefined });
    reportError(this.props.where, error, info.componentStack ?? undefined);
  }

  private retry = () => {
    if (this.props.where === "app") window.location.reload();
    else this.setState({ error: undefined, component: undefined, note: undefined });
  };

  private copy = () => {
    const { error, component } = this.state;
    const details = [`CheapTrip ${window.location.hash || "#/"}`, error?.stack ?? String(error), component ?? ""].join("\n\n");
    Promise.resolve(navigator.clipboard?.writeText(details))
      .then(() => this.setState({ note: "Copied: paste it wherever you report the problem." }))
      .catch(() => this.setState({ note: "Couldn't copy: the details are in the log folder too." }));
  };

  private openLogs = () => {
    api.open("logs").catch((err: Error) => this.setState({ note: err.message }));
  };

  render(): ReactNode {
    const { error, note } = this.state;
    if (!error) return this.props.children;
    const screen = this.props.where === "screen";
    return (
      <div className={screen ? "flex justify-center py-space-xl" : "flex min-h-screen items-center justify-center bg-background p-space-xl"}>
        <section role="alert" className="card flex w-full max-w-2xl flex-col gap-space-lg p-space-xl">
          <div className="flex items-start gap-space-lg">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-error-container/30 text-error">
              <Icon name="error" size={28} />
            </span>
            <div className="flex flex-col gap-space-xs">
              <h2 className="text-headline-sm text-on-surface">{screen ? "This screen hit a problem" : "CheapTrip hit a problem"}</h2>
              <p className="text-body text-on-surface-variant">
                {screen ? "The rest of CheapTrip works as usual, and it keeps searching in the background. " : "It keeps searching in the background. "}
                What went wrong is saved in the log, so it can be fixed.
              </p>
            </div>
          </div>
          <pre className="well max-h-48 overflow-auto p-space-md font-mono text-mono-sm whitespace-pre-wrap text-on-surface-variant">
            {error.message || error.name}
          </pre>
          <div className="flex flex-wrap items-center gap-space-sm">
            <button className="btn-primary" onClick={this.retry}>
              <Icon name="refresh" size={18} />{screen ? "Try this screen again" : "Reload CheapTrip"}
            </button>
            <button className="btn-secondary" onClick={this.copy}><Icon name="content_copy" size={18} />Copy the details</button>
            <button className="btn-secondary" onClick={this.openLogs}><Icon name="folder_open" size={18} />Open the log folder</button>
          </div>
          {note && <p className="text-caption text-on-surface-variant" role="status">{note}</p>}
        </section>
      </div>
    );
  }
}
