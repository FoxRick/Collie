import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  /** Settings tab key that owns the crashing subtree — shown in the fallback. */
  tab: string
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Per-tab error boundary for Settings. Without it, any throw inside a tab
 * unmounts the whole React tree and the user is left staring at a blank
 * window (no global error boundary exists in the renderer). The boundary
 * keeps the sidebar alive and offers a way back instead.
 */
export default class TabErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // No telemetry channel yet — keep it on the console for diagnosis.
    console.error(`Settings tab "${this.props.tab}" crashed:`, error, info.componentStack)
  }

  componentDidUpdate(prevProps: Props): void {
    // Switching tabs gets a fresh chance — a stale error must not pin the UI.
    if (prevProps.tab !== this.props.tab && this.state.error) {
      this.setState({ error: null })
    }
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <section className="settings-card">
          <div className="settings-card-icon">⚠️</div>
          <div>
            <h3>This section hit a snag</h3>
            <p className="settings-lead">
              Something went wrong here, but the rest of Collie is fine. Try
              again — if it keeps happening, restart the app.
            </p>
            <button
              type="button"
              className="settings-button"
              onClick={() => this.setState({ error: null })}
            >
              Try again
            </button>
          </div>
        </section>
      )
    }
    return this.props.children
  }
}
