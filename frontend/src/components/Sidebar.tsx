import type { Tab } from '../types/project'

type Props = {
  menuOpen: boolean
  activeTab: Tab
  savedCount: number
  onToggle: () => void
  onClose: () => void
  onNavigate: (tab: Tab) => void
}

const items: { tab: Tab; icon: string; title: string }[] = [
  { tab: 'home', icon: '⌂', title: 'Home' },
  { tab: 'history', icon: '◷', title: 'History' },
  { tab: 'projects', icon: '▦', title: 'Projects' },
  { tab: 'settings', icon: '⚙', title: 'Settings' },
  { tab: 'help', icon: '?', title: 'Help & Support' },
]

export function Sidebar({ menuOpen, activeTab, savedCount, onToggle, onClose, onNavigate }: Props) {
  return (
    <>
      <div className="topbar">
        <button
          type="button"
          className="menu-toggle"
          aria-label={menuOpen ? 'Close navigation menu' : 'Open navigation menu'}
          aria-expanded={menuOpen}
          aria-controls="app-navigation"
          onClick={onToggle}
        ><span /><span /><span /></button>
        <span className="topbar-brand"><span className="brand-mark">G</span> GENOVATION <span className="brand-subtitle">/ AI STUDIO</span></span>
        <span className="topbar-status"><span className="status-dot" /> Workspace</span>
      </div>
      {menuOpen && <button type="button" className="nav-backdrop" aria-label="Close menu" onClick={onClose} />}
      <aside id="app-navigation" className={`side-nav ${menuOpen ? 'side-nav-open' : ''}`} aria-hidden={!menuOpen}>
        <div className="nav-caption">WORKSPACE</div>
        {items.map(({ tab, icon, title }) => (
          <button
            type="button"
            key={tab}
            className={`nav-item ${activeTab === tab ? 'nav-item-active' : ''}`}
            tabIndex={menuOpen ? 0 : -1}
            onClick={() => onNavigate(tab)}
          >
            <span className="nav-icon" aria-hidden="true">{icon}</span><span>{title}</span>
            {tab === 'projects' && savedCount > 0 && <span className="nav-soon">{savedCount}</span>}
          </button>
        ))}
        <div className="nav-footer">GENOVATION AI <span>·</span> Preview workspace</div>
      </aside>
    </>
  )
}
