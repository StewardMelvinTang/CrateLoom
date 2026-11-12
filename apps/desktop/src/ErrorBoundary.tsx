import {Component, type ReactNode} from 'react';
import {message} from './engine';

/** Keeps one broken screen from blanking the whole window, and says how to recover. */
export class ErrorBoundary extends Component<{children: ReactNode}, {error: unknown}> {
  state = {error: null as unknown};
  static getDerivedStateFromError(error: unknown) { return {error}; }
  componentDidCatch(error: unknown) { console.error(error); }
  render() {
    if (this.state.error === null) return this.props.children;
    return <div className="crash" role="alert">
      <h1>CrateLoom could not display this screen</h1>
      <p>Your library, cues and audio files are not affected. Try again, or reload the window. If it keeps happening with one track, analyse that track again.</p>
      <pre>{message(this.state.error)}</pre>
      <div className="crash-actions">
        <button className="primary" onClick={() => this.setState({error: null})}>Try again</button>
        <button onClick={() => window.location.reload()}>Reload window</button>
      </div>
    </div>;
  }
}
