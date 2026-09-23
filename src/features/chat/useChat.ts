import { useState, useEffect, useCallback, useRef } from 'react'
import type { Message, StreamStep } from '../../types'

export function useChat(activeId: string | null, onDone?: () => void) {
  const [messages, setMessages] = useState<Message[]>([])
  const [isGenerating, setIsGenerating] = useState(false)
  const [streamContent, setStreamContent] = useState('')
  const [streamSteps, setStreamSteps] = useState<StreamStep[]>([])
  const activeIdRef = useRef(activeId)
  useEffect(() => { activeIdRef.current = activeId }, [activeId])
  const onDoneRef = useRef(onDone)
  useEffect(() => { onDoneRef.current = onDone }, [onDone])

  useEffect(() => {
    if (activeId) {
      loadMessages(activeId)
      // Clear streaming state from previous conversation to avoid content mixing
      setStreamContent('')
      setStreamSteps([])
    } else {
      // Conversation deleted or deselected — clear all chat state
      setMessages([])
      setStreamContent('')
      setStreamSteps([])
      setIsGenerating(false)
    }
  }, [activeId])

  useEffect(() => {
    const off = window.electronAPI.on('chat:stream', (data: unknown) => {
      const { type, data: payload } = data as { type: string; data: string }
      handleStreamEvent(type, payload)
    })
    return off
  }, [])

  const handleStreamEvent = (type: string, payload: string) => {
    try {
      const parsed = JSON.parse(payload)
      if (type === 'reasoning') {
        setStreamSteps((prev) => [...prev, { type: 'reasoning', content: parsed.content }])
      } else if (type === 'thought') {
        setStreamSteps((prev) => [...prev, { type: 'thought', content: parsed.content }])
      } else if (type === 'action') {
        setStreamSteps((prev) => [...prev, { type: 'action', tool: parsed.tool, input: parsed.input }])
      } else if (type === 'observation') {
        setStreamSteps((prev) => {
          const steps = [...prev]
          for (let i = steps.length - 1; i >= 0; i--) {
            const step = steps[i]
            if (step.type === 'action' && !step.output) {
              steps[i] = { ...step, output: parsed.content }
              break
            }
          }
          return steps
        })
      } else if (type === 'answer_chunk') {
        setStreamContent((prev) => prev + (parsed.content || ''))
      } else if (type === 'done') {
        setIsGenerating(false)
        // Keep streaming state visible - no need to re-read from DB
        onDoneRef.current?.()
      }
    } catch {
      // partial data
    }
  }

  const loadMessages = async (conversationId: string) => {
    const res = await window.electronAPI.invoke('conversations:get', conversationId)
    setMessages((res as { messages: Message[] }).messages || [])
  }

  const handleSendMessage = useCallback(
    async (content: string) => {
      if (!activeId || isGenerating) return

      // Clear previous stream state before starting new conversation
      setStreamContent('')
      setStreamSteps([])

      const userMsg: Message = {
        id: crypto.randomUUID(),
        conversation_id: activeId,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, userMsg])
      setIsGenerating(true)
      setStreamContent('')
      setStreamSteps([])

      try {
        await window.electronAPI.invoke('chat:send', {
          conversationId: activeId,
          message: content,
        })
      } catch (err) {
        console.error('Chat error:', err)
        setIsGenerating(false)
      }
    },
    [activeId, isGenerating],
  )

  const handleStopGenerate = useCallback(() => {
    window.electronAPI.invoke('chat:cancel')
    setIsGenerating(false)
  }, [])

  return {
    messages,
    isGenerating,
    streamContent,
    streamSteps,
    handleSendMessage,
    handleStopGenerate,
  }
}
