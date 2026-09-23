export interface MenuItemDef {
  id?: string
  label?: string
  shortcut?: string
  type?: 'separator'
  action?: string
  children?: MenuItemDef[]
}

export function buildMenuDef(t: (key: string) => string): MenuItemDef[] {
  return [
    {
      label: t('menu.file'),
      children: [
        { id: 'settings', label: t('menu.settings'), shortcut: 'Ctrl+,', action: 'open-settings' },
        { type: 'separator' },
        { id: 'quit', label: t('menu.quit'), shortcut: 'Ctrl+Q', action: 'quit' },
      ],
    },
    {
      label: t('menu.edit'),
      children: [
        { id: 'undo', label: t('menu.undo'), shortcut: 'Ctrl+Z', action: 'undo' },
        { id: 'redo', label: t('menu.redo'), shortcut: 'Ctrl+Y', action: 'redo' },
        { type: 'separator' },
        { id: 'cut', label: t('menu.cut'), shortcut: 'Ctrl+X', action: 'cut' },
        { id: 'copy', label: t('menu.copy'), shortcut: 'Ctrl+C', action: 'copy' },
        { id: 'paste', label: t('menu.paste'), shortcut: 'Ctrl+V', action: 'paste' },
        { id: 'selectAll', label: t('menu.selectAll'), shortcut: 'Ctrl+A', action: 'selectAll' },
      ],
    },
    {
      label: t('menu.view'),
      children: [
        { id: 'zoomIn', label: t('menu.zoomIn'), shortcut: 'Ctrl+=', action: 'zoomIn' },
        { id: 'zoomOut', label: t('menu.zoomOut'), shortcut: 'Ctrl+-', action: 'zoomOut' },
        { id: 'zoomReset', label: t('menu.zoomReset'), shortcut: 'Ctrl+0', action: 'zoomReset' },
      ],
    },
    {
      label: t('menu.help'),
      children: [
        { id: 'about', label: t('menu.about'), action: 'about' },
        { type: 'separator' },
        { id: 'viewLogs', label: t('menu.viewLogs'), action: 'view-logs' },
        { id: 'exportLogs', label: t('menu.exportLogs'), action: 'export-logs' },
      ],
    },
  ]
}
