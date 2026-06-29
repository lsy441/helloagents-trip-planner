import axios from 'axios'
import type { TripFormData, TripPlanResponse, FeedbackRequest } from '@/types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.trim() || ''

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000,
  headers: {
    'Content-Type': 'application/json'
  }
})

apiClient.interceptors.request.use(
  (config) => {
    console.log('Request:', config.method?.toUpperCase(), config.url)
    return config
  },
  (error) => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

apiClient.interceptors.response.use(
  (response) => {
    console.log('Response:', response.status, response.config.url)
    return response
  },
  (error) => {
    console.error('Response error:', error.response?.status, error.message)
    return Promise.reject(error)
  }
)

const SESSION_KEY = 'trip_session_id'

let _sessionId: string | null = null

export function getSessionId(): string | null {
  if (!_sessionId) {
    _sessionId = localStorage.getItem(SESSION_KEY)
  }
  return _sessionId
}

export function setSessionId(sid: string) {
  _sessionId = sid
  localStorage.setItem(SESSION_KEY, sid)
  sessionStorage.setItem(SESSION_KEY, sid)
}

export function clearSessionId() {
  _sessionId = null
  localStorage.removeItem(SESSION_KEY)
  sessionStorage.removeItem(SESSION_KEY)
}

export async function generateTripWithContext(sessionId: string | null, tripRequest: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan-with-context', {
      session_id: sessionId,
      trip_request: tripRequest
    })
    if (response.data.session_id) {
      setSessionId(response.data.session_id)
    }
    return response.data
  } catch (error: any) {
    console.error('Generate trip with context failed:', error)
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

export async function updatePlanWithFeedback(feedbackReq: FeedbackRequest): Promise<TripPlanResponse> {
  try {
    const params: any = {}
    const sid = getSessionId()
    if (sid) {
      params.session_id = sid
    }
    const response = await apiClient.post<TripPlanResponse>('/api/trip/feedback', feedbackReq, { params })
    if (response.data.session_id) {
      setSessionId(response.data.session_id)
    }
    return response.data
  } catch (error: any) {
    console.error('Feedback failed:', error)
    throw new Error(error.response?.data?.detail || error.message || '反馈调整失败')
  }
}

export async function generateTripPlanStream(
  formData: TripFormData,
  onProgress?: (message: string) => void
): Promise<TripPlanResponse> {
  const sid = getSessionId()
  const url = `${API_BASE_URL}/api/trip/plan-stream${sid ? `?session_id=${sid}` : ''}`

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(formData)
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('Stream not supported')
  }

  return new Promise((resolve, reject) => {
    const decoder = new TextDecoder()
    let buffer = ''

    function readStream() {
      reader?.read().then(({ done, value }) => {
        if (done) {
          reject(new Error('Stream ended without result'))
          return
        }

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'start' && data.session_id) {
                setSessionId(data.session_id)
              }
              if (data.type === 'progress' && onProgress) {
                onProgress(data.message)
              } else if (data.type === 'result') {
                resolve(data as unknown as TripPlanResponse)
              } else if (data.type === 'error') {
                reject(new Error(data.message))
              }
            } catch (e) { }
          }
        }

        readStream()
      }).catch(reject)
    }

    readStream()
  })
}

export interface SessionInfo {
  session_id: string
  title: string
  message_count: number
  updated_at: string | null
}

export interface SessionDetail {
  session_id: string
  messages: { role: string; content: string }[]
}

export async function getChatSessions(): Promise<SessionInfo[]> {
  try {
    const response = await apiClient.get<{ sessions: SessionInfo[] }>('/api/chat/sessions')
    return response.data.sessions
  } catch (error: any) {
    console.error('Get sessions failed:', error)
    return []
  }
}

export async function getChatSession(sessionId: string): Promise<SessionDetail | null> {
  try {
    const response = await apiClient.get<SessionDetail>(`/api/chat/sessions/${sessionId}`)
    return response.data
  } catch (error: any) {
    console.error('Get session detail failed:', error)
    return null
  }
}

export async function deleteChatSession(sessionId: string): Promise<boolean> {
  try {
    await apiClient.delete(`/api/chat/sessions/${sessionId}`)
    if (getSessionId() === sessionId) {
      clearSessionId()
    }
    return true
  } catch (error: any) {
    console.error('Delete session failed:', error)
    return false
  }
}

export default apiClient
