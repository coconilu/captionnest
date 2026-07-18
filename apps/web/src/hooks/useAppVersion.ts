import { useEffect, useState } from 'react'

import { readDesktopVersion, type DesktopVersionState } from '../lib/appVersion'

const initialState: DesktopVersionState = { status: 'loading', version: null }

export function useAppVersion() {
  const [state, setState] = useState<DesktopVersionState>(initialState)

  useEffect(() => {
    let active = true
    void readDesktopVersion().then((result) => {
      if (active) setState(result)
    })
    return () => {
      active = false
    }
  }, [])

  return state
}
