import './index.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { HttpTransport, takeToken } from '@/api/transport'
import { forgetImage } from '@/store/images'
import { connect, failStartup, useSession } from '@/store/session'
import { forgetDocument } from '@/store/view'
import { startViewSync } from '@/store/viewSync'

import { App } from './App'

startViewSync()
const token = takeToken()
if (token) void connect(new HttpTransport(token))
else failStartup('This page was opened without an access token.')

// drop the view state and images of closed documents
useSession.subscribe((state, previous) => {
  if (state.documents === previous.documents) return
  for (const id of Object.keys(previous.documents)) {
    if (!(id in state.documents)) {
      forgetImage(id)
      forgetDocument(id)
    }
  }
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
