import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { io, type Socket } from 'socket.io-client'
import { toast } from 'sonner'
import { type AlertCategories, useAlertStore } from '@/stores/alertStore'
import { useAuthStore } from '@/stores/authStore'
import { useSessionStore } from '@/stores/sessionStore'
// SUDARSHAN: the shared connection and the app-wide order alerts (src/sudarshan/books).
import { claimOrderSound } from '@/sudarshan/books/chime'
import { installOrderAlerts, noteAppCancelEvent } from '@/sudarshan/books/orderAlerts'
// SUDARSHAN M6.9: Exit all's result shows once, whoever speaks first.
import { EXIT_ALL_TOAST } from '@/sudarshan/books/sendOutcome'
import { acquireSharedSocket, releaseSharedSocket } from '@/sudarshan/books/sharedSocket'

// Audio throttling configuration
const AUDIO_THROTTLE_MS = 1000

interface OrderEventData {
  symbol: string
  action: string
  orderid: string
  batch_order?: boolean
  is_last_order?: boolean
}

interface CancelOrderEventData {
  orderid: string
  batch_order?: boolean
}

interface ModifyOrderEventData {
  orderid: string
  status: string
}

interface ClosePositionEventData {
  message: string
  status: string
  /** SUDARSHAN M6.9: some exits may have gone through. */
  not_confirmed?: boolean
}

interface MasterContractData {
  message: string
}

interface AnalyzerUpdateData {
  request: {
    api_type: string
    symbol?: string
    action?: string
    quantity?: string
    orderid?: string
    position_size?: string
  }
  response: {
    status: string
    message?: string
    orderid?: string
    canceled_orders?: string[]
  }
}

// Helper to show toast only if enabled for category
const showCategoryToast = (
  type: 'success' | 'error' | 'warning' | 'info',
  message: string,
  category?: keyof AlertCategories,
  // SUDARSHAN M6.9: a toast another sender of the same result shares.
  id?: string
) => {
  const { shouldShowToast } = useAlertStore.getState()
  if (shouldShowToast(category)) {
    if (id) toast[type](message, { id })
    else toast[type](message)
  }
}

export function useSocket() {
  const navigate = useNavigate()
  // SUDARSHAN: `navigate` changes identity with every pathname; read through a
  // ref so a page change does not re-run the connection effect below.
  const navigateRef = useRef(navigate)
  navigateRef.current = navigate
  const { isAuthenticated } = useAuthStore()
  const socketRef = useRef<Socket | null>(null)
  // Reactive copy so consumers (SocketProvider -> useOrderEventRefresh) can share
  // this ONE connection instead of each opening their own (browser per-host
  // connection limit is shared across tabs; extra polling sockets exhaust it).
  const [socketInstance, setSocketInstance] = useState<Socket | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const lastAudioTimeRef = useRef<number>(0)
  const audioEnabledRef = useRef<boolean>(false)

  const playAlertSound = useCallback((category?: keyof AlertCategories) => {
    // Check if sound should play based on alert settings
    const { shouldPlaySound, shouldShowToast } = useAlertStore.getState()
    if (!shouldPlaySound()) return
    // If category specified, also check category is enabled
    if (category && !shouldShowToast(category)) return

    const now = Date.now()
    const timeSinceLastAttempt = now - lastAudioTimeRef.current

    if (timeSinceLastAttempt < AUDIO_THROTTLE_MS && lastAudioTimeRef.current !== 0) {
      return
    }

    lastAudioTimeRef.current = now

    // SUDARSHAN: one sound per moment with the order alert chime (an order
    // placed and filled at once would otherwise sound twice).
    if ((category === 'orders' || category === 'analyzer') && !claimOrderSound(now)) return

    if (audioRef.current) {
      audioRef.current
        .play()
        .then(() => {
          audioEnabledRef.current = true
        })
        .catch(() => {})
    }
  }, [])

  const enableAudio = useCallback(() => {
    if (!audioEnabledRef.current && audioRef.current) {
      const audio = audioRef.current
      const originalVolume = audio.volume
      audio.volume = 0
      audio
        .play()
        .then(() => {
          audio.pause()
          audio.currentTime = 0
          audio.volume = originalVolume
          audioEnabledRef.current = true
        })
        .catch(() => {
          audio.volume = originalVolume
        })
    }
  }, [])

  useEffect(() => {
    // Only connect when authenticated
    if (!isAuthenticated) {
      return
    }

    // Create audio element
    audioRef.current = new Audio('/sounds/alert.mp3')
    audioRef.current.preload = 'auto'

    // Enable audio on user interaction
    const handleInteraction = () => {
      enableAudio()
    }

    ;['click', 'touchstart', 'keydown'].forEach((eventType) => {
      document.addEventListener(eventType, handleInteraction, { once: true, passive: true })
    })

    // Connect to socket server
    const protocol = window.location.protocol
    const host = window.location.hostname
    const port = window.location.port

    // Use polling transport only - WebSocket upgrade fails with threading async mode
    // Polling is still real-time via HTTP long-polling
    // SUDARSHAN: borrowed for the signed-in tab, not opened per mount, so a page
    // or layout change never drops what the server pushes (sudarshan/books/sharedSocket.ts).
    const shared = acquireSharedSocket(() =>
      io(`${protocol}//${host}:${port}`, {
        transports: ['polling'],
        upgrade: false,
        reconnectionAttempts: 5,
        reconnectionDelay: 1000,
        timeout: 20000,
        forceNew: true, // Always create new session to avoid "Invalid session" errors on reconnect
      })
    )
    socketRef.current = shared
    setSocketInstance(shared)

    // SUDARSHAN: the handlers below register through this recorder, so cleanup
    // removes exactly this provider's from the connection it shares.
    const registered: Array<[string, (...args: any[]) => void]> = []
    const socket = {
      on(event: string, handler: (...args: any[]) => void) {
        shared.on(event, handler)
        registered.push([event, handler])
        return socket
      },
    }

    // Force logout from another device
    socket.on('force_logout', (data: { message: string }) => {
      playAlertSound('system')
      // Clear auth store immediately
      useAuthStore.getState().logout()
      useSessionStore.getState().setActiveSessionCount(0)
      // Show persistent toast and redirect after a short delay so user sees it
      toast.error(data.message || 'Logged out from another device', {
        duration: 10000,
      })
      setTimeout(() => {
        navigateRef.current('/login', { replace: true })
      }, 2000)
    })

    // Password change notification
    socket.on('password_change', (data: { message: string }) => {
      playAlertSound('system')
      showCategoryToast('info', data.message, 'system')
    })

    // Master contract download notification
    socket.on('master_contract_download', (data: MasterContractData) => {
      playAlertSound('system')
      showCategoryToast('info', `Master Contract: ${data.message}`, 'system')
    })

    // Cancel order notification - only play sound, UI handles toast
    socket.on('cancel_order_event', (data: CancelOrderEventData) => {
      // SUDARSHAN: a cancel sent from this app, so the broker's echo is not announced.
      noteAppCancelEvent('cancel_order_event', data)
      if (!data.batch_order) {
        playAlertSound('orders')
      }
    })

    // Modify order notification - only play sound, UI handles toast
    socket.on('modify_order_event', (_data: ModifyOrderEventData) => {
      playAlertSound('orders')
    })

    // Close position notification
    socket.on('close_position_event', (data: ClosePositionEventData) => {
      playAlertSound('orders')
      // SUDARSHAN M6.9: a close that did not close everything shows as one, never as a
      // success; it shares its toast with the Positions page's own answer to it.
      if (data.status === 'error') {
        showCategoryToast(
          data.not_confirmed ? 'warning' : 'error',
          data.message || 'Exit all did not close every position. Check Positions.',
          'positions',
          EXIT_ALL_TOAST
        )
        return
      }
      showCategoryToast('success', data.message || 'All Open Positions Squared Off', 'positions')
    })

    // Order placement notification
    socket.on('order_event', (data: OrderEventData) => {
      const shouldPlayAudio = !data.batch_order || data.is_last_order
      if (shouldPlayAudio) {
        playAlertSound('orders')
      }

      const message = `${data.action.toUpperCase()} Order Placed for Symbol: ${data.symbol}, Order ID: ${data.orderid}`
      if (data.action.toUpperCase() === 'BUY') {
        showCategoryToast('success', message, 'orders')
      } else {
        showCategoryToast('error', message, 'orders')
      }
    })

    // Generic order notification handler
    socket.on(
      'order_notification',
      (data: { symbol?: string; status?: string; message?: string }) => {
        playAlertSound('orders')

        let type: 'success' | 'error' | 'warning' | 'info' = 'info'
        if (data.status && typeof data.status === 'string') {
          if (data.status.toLowerCase().includes('success')) {
            type = 'success'
          } else if (
            data.status.toLowerCase().includes('error') ||
            data.status.toLowerCase().includes('reject')
          ) {
            type = 'error'
          } else if (data.status.toLowerCase().includes('pending')) {
            type = 'warning'
          }
        }

        let message = ''
        if (data.symbol) {
          message += `${data.symbol}: `
        }
        if (data.status) {
          message += data.status
        }
        if (data.message) {
          message += data.message
        }

        showCategoryToast(type, message, 'orders')
      }
    )

    // Active sessions update (event-driven, no polling)
    socket.on('active_sessions_update', (data: { count: number }) => {
      useSessionStore.getState().setActiveSessionCount(data.count)
    })

    // Analyzer update notification
    socket.on('analyzer_update', (data: AnalyzerUpdateData) => {
      // SUDARSHAN: a sandbox cancel sent from this app, so its echo is not announced.
      noteAppCancelEvent('analyzer_update', data)
      const passiveApiTypes = [
        'orderstatus',
        'openposition',
        'orderbook',
        'tradebook',
        'positions',
        'holdings',
      ]
      const isPassiveMonitoring = passiveApiTypes.includes(data.request.api_type)

      // SUDARSHAN: the sandbox engine's own updates (a fill, a square-off) carry
      // no request type; their orders' alerts sound once, and the order alert
      // mute silences them (src/sudarshan/books/orderAlerts.ts).
      const isSandboxEngine = !data.request?.api_type

      if (!isPassiveMonitoring && !isSandboxEngine) {
        playAlertSound('analyzer')
      }

      let message = ''
      let type: 'success' | 'error' | 'info' =
        data.response.status === 'success' ? 'success' : 'error'

      const action = data.request.action || ''
      const symbol = data.request.symbol || ''
      const quantity = data.request.quantity || ''
      const orderid = data.response.orderid || data.request.orderid || ''
      const apiType = data.request.api_type || ''

      if (data.response.status === 'error') {
        message = `Error: ${data.response.message}`
        if (symbol) message = `${symbol} - ${message}`
      } else if (apiType === 'cancelorder') {
        message = orderid ? `Order Cancelled - ID: ${orderid}` : 'Order Cancelled'
      } else if (apiType === 'cancelallorder') {
        message = data.response.message || 'All Orders Cancelled'
      } else if (apiType === 'modifyorder') {
        message = orderid ? `Order Modified - ID: ${orderid}` : 'Order Modified'
      } else if (apiType === 'closeposition') {
        message = data.response.message || 'Position Closed'
      } else if (
        apiType === 'placesmartorder' &&
        data.response.message &&
        (data.response.message.includes('Positions Already Matched') ||
          data.response.message.includes('No OpenPosition Found'))
      ) {
        message = data.response.message
        type = 'info'
      } else {
        if (!action && !symbol && !orderid) {
          return
        }

        if (action && symbol) {
          message = `${action} Order Placed for Symbol: ${symbol}`
          if (quantity) message += `, Qty: ${quantity}`
          if (orderid) message += `, Order ID: ${orderid}`

          if (apiType === 'placesmartorder' && data.request.position_size) {
            message += `, Size: ${data.request.position_size}`
          }
        } else if (orderid) {
          message = `Order Placed - ID: ${orderid}`
        } else {
          return
        }
      }

      if (message) {
        showCategoryToast(type, message, 'analyzer')
      }
    })

    // SUDARSHAN: fills, partial fills, rejections and broker cancels (M6.8).
    const removeOrderAlerts = installOrderAlerts(shared)

    return () => {
      // SUDARSHAN: remove this provider's handlers and give the connection back;
      // it closes only if no provider takes it again (logout, leaving the app pages).
      removeOrderAlerts()
      for (const [event, handler] of registered) shared.off(event, handler)
      releaseSharedSocket(shared)
      setSocketInstance(null)
      ;['click', 'touchstart', 'keydown'].forEach((eventType) => {
        document.removeEventListener(eventType, handleInteraction)
      })
    }
  }, [isAuthenticated, playAlertSound, enableAudio])

  return {
    socket: socketInstance,
    playAlertSound,
  }
}
