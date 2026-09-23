import { useState, useEffect, useCallback } from 'react'
import type { Conversation } from '../../types'

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)

  const loadConversations = async () => {
    const res = await window.electronAPI.invoke('conversations:list')
    setConversations(res as Conversation[])
  }

  useEffect(() => {
    loadConversations()
  }, [])

  const handleNewChat = useCallback(async () => {
    const res = await window.electronAPI.invoke('conversations:create')
    const conv = res as Conversation
    setConversations((prev) => [conv, ...prev])
    setActiveId(conv.id)
  }, [])

  const handleDeleteConversation = useCallback(
    async (id: string) => {
      await window.electronAPI.invoke('conversations:delete', id)
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (activeId === id) {
        setActiveId(null)
      }
    },
    [activeId],
  )

  const handleRenameConversation = useCallback(
    async (id: string, title: string) => {
      await window.electronAPI.invoke('conversations:rename', { id, title })
      setConversations((prev) =>
        prev.map((c) => (c.id === id ? { ...c, title } : c)),
      )
    },
    [],
  )

  return {
    conversations,
    activeId,
    setActiveId,
    loadConversations,
    handleNewChat,
    handleDeleteConversation,
    handleRenameConversation,
  }
}
