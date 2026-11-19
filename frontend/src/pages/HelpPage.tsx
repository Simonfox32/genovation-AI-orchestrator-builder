type Props = { onNewProject: () => void }

export function HelpPage({ onNewProject }: Props) {
  return (
    <main className="container workspace-container">
      <div className="workspace-heading">
        <div><span className="eyebrow">WORKSPACE / SUPPORT</span><h1>Help & Support</h1><p>Answers to common questions about the AI Orchestrator.</p></div>
        <button type="button" className="workspace-primary" onClick={onNewProject}>Open Project Builder</button>
      </div>
      <section className="workspace-help">
        <details open><summary>How do I generate a project?</summary><p>On Home, describe what you want to build, select an optimization preference, and click Generate Project. The frontend sends your request to the configured backend.</p></details>
        <details><summary>Where do my previous projects appear?</summary><p>History keeps the IDs of projects you create in this browser. Use Open to retrieve a project's current status from the API. History is not a backend-wide list.</p></details>
        <details><summary>What is Saved Projects?</summary><p>Save important projects from Home or History to bookmark them in your Projects tab. Bookmarks stay in this browser only.</p></details>
        <details><summary>Why does project generation fail?</summary><p>Check that the backend is running and that VITE_API_BASE_URL and backend CORS settings are correct. You can test the backend endpoint on the Settings page.</p></details>
        <details><summary>Can I download generated code?</summary><p>If the backend has produced a downloadable artifact, open that project on Home and use Download project (.zip).</p></details>
      </section>
    </main>
  )
}
