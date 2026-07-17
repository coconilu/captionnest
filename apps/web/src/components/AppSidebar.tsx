import { Boxes, ListVideo, Settings } from 'lucide-react'

export type AppView = 'tasks' | 'services' | 'settings'

interface AppSidebarProps {
  activeView: AppView
  onSelect: (view: AppView) => void
}

const NAV_ITEMS = [
  { id: 'tasks', label: '任务', icon: ListVideo },
  { id: 'services', label: '模型与服务', icon: Boxes },
  { id: 'settings', label: '设置', icon: Settings },
] as const

export function AppSidebar({ activeView, onSelect }: AppSidebarProps) {
  return (
    <nav className="app-sidebar" aria-label="主导航">
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon
        const active = item.id === activeView
        return (
          <button
            key={item.id}
            type="button"
            className={active ? 'is-active' : undefined}
            aria-current={active ? 'page' : undefined}
            onClick={() => onSelect(item.id)}
          >
            <Icon size={16} aria-hidden="true" />
            <span>{item.label}</span>
          </button>
        )
      })}
    </nav>
  )
}
